#include <mpi.h>
#include <cuda_runtime.h>

#ifndef QUEST_COMPRESSION_ENABLE_NVTX
#define QUEST_COMPRESSION_ENABLE_NVTX 0
#endif

#if QUEST_COMPRESSION_ENABLE_NVTX
#include <nvtx3/nvToolsExt.h>
#endif

#include <nvcomp.hpp>
#include <nvcomp/nvcompManager.hpp>
#if QUEST_COMPRESSION_HAVE_NVCOMP_LZ4
#include <nvcomp/lz4.hpp>
#endif
#if QUEST_COMPRESSION_HAVE_NVCOMP_GDEFLATE
#include <nvcomp/gdeflate.hpp>
#endif
#if QUEST_COMPRESSION_HAVE_NVCOMP_BITCOMP
#include <nvcomp/bitcomp.hpp>
#endif

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <vector>

class ScopedNvtxRange {
public:
  explicit ScopedNvtxRange(const char* label)
  {
#if QUEST_COMPRESSION_ENABLE_NVTX
    nvtxRangePushA(label);
#else
    (void)label;
#endif
  }

  ~ScopedNvtxRange()
  {
#if QUEST_COMPRESSION_ENABLE_NVTX
    nvtxRangePop();
#endif
  }
};

struct Complex64 {
  double real;
  double imag;
};

struct Options {
  std::string source_kind = "synthetic";
  std::string pattern = "zero_sparse";
  std::string checkpoint = "synthetic";
  std::string exchange_shape = "amps_to_buffers";
  std::string codec = "raw";
  std::string payload_template;
  std::string output = "-";
  std::string allocation_id = "manual";
  std::size_t payload_amps = 1u << 20;
  std::size_t chunk_amps = 0;
  std::size_t nvcomp_chunk_bytes = 1u << 20;
  int warmup = 1;
  int reps = 7;
};

struct Timing {
  double d2h = 0.0;
  double compress = 0.0;
  double size_exchange = 0.0;
  double mpi = 0.0;
  double h2d = 0.0;
  double decompress = 0.0;
  double total = 0.0;
  std::uint64_t compressed_bytes = 0;
  int fallback_used = 0;
  std::string status = "PASS";
  std::string verify_status = "PASS";
};

static void usage(const char* prog)
{
  std::fprintf(stderr,
    "Usage: %s [options]\n"
    "  --source-kind synthetic|genuine\n"
    "  --pattern NAME\n"
    "  --checkpoint NAME\n"
    "  --exchange-shape amps_to_buffers|sub_buffers\n"
    "  --codec raw|nvcomp_lz4|nvcomp_gdeflate|nvcomp_bitcomp\n"
    "  --payload-amps N\n"
    "  --chunk-amps N|0(full)\n"
    "  --payload-template PATH_WITH_{rank}   Required for genuine data\n"
    "  --output PATH|-                         Append TSV rows or stdout\n"
    "  --warmup N --reps N\n",
    prog);
}

static bool parse_size(const char* text, std::size_t& out)
{
  char* end = nullptr;
  unsigned long long value = std::strtoull(text, &end, 10);
  if (text == nullptr || text[0] == '\0' || end == nullptr || *end != '\0')
    return false;
  out = static_cast<std::size_t>(value);
  return true;
}

static bool parse_int(const char* text, int& out)
{
  char* end = nullptr;
  long value = std::strtol(text, &end, 10);
  if (text == nullptr || text[0] == '\0' || end == nullptr || *end != '\0' || value < 0 || value > 2147483647L)
    return false;
  out = static_cast<int>(value);
  return true;
}

static Options parse_options(int argc, char** argv)
{
  Options opts;
  for (int i = 1; i < argc; i++) {
    std::string arg = argv[i];
    if (arg == "--help" || arg == "-h") {
      usage(argv[0]);
      std::exit(EXIT_SUCCESS);
    }
    if (i + 1 >= argc)
      throw std::runtime_error("missing value for " + arg);
    std::string value = argv[++i];
    if (arg == "--source-kind") opts.source_kind = value;
    else if (arg == "--pattern") opts.pattern = value;
    else if (arg == "--checkpoint") opts.checkpoint = value;
    else if (arg == "--exchange-shape") opts.exchange_shape = value;
    else if (arg == "--codec") opts.codec = value;
    else if (arg == "--payload-template") opts.payload_template = value;
    else if (arg == "--output") opts.output = value;
    else if (arg == "--allocation-id") opts.allocation_id = value;
    else if (arg == "--payload-amps") {
      if (!parse_size(value.c_str(), opts.payload_amps)) throw std::runtime_error("bad --payload-amps");
    } else if (arg == "--chunk-amps") {
      if (!parse_size(value.c_str(), opts.chunk_amps)) throw std::runtime_error("bad --chunk-amps");
    } else if (arg == "--nvcomp-chunk-bytes") {
      if (!parse_size(value.c_str(), opts.nvcomp_chunk_bytes)) throw std::runtime_error("bad --nvcomp-chunk-bytes");
    } else if (arg == "--warmup") {
      if (!parse_int(value.c_str(), opts.warmup)) throw std::runtime_error("bad --warmup");
    } else if (arg == "--reps") {
      if (!parse_int(value.c_str(), opts.reps) || opts.reps <= 0) throw std::runtime_error("bad --reps");
    } else {
      throw std::runtime_error("unknown option " + arg);
    }
  }
  if (opts.source_kind == "genuine" && opts.payload_template.empty())
    throw std::runtime_error("--payload-template is required for genuine source kind");
  return opts;
}

static void check_cuda(cudaError_t err, const char* call)
{
  if (err != cudaSuccess)
    throw std::runtime_error(std::string(call) + " failed: " + cudaGetErrorString(err));
}

#define CUDA_CHECK(call) check_cuda((call), #call)

static void check_nvcomp(nvcompStatus_t status, const char* where)
{
  if (status != nvcompSuccess)
    throw std::runtime_error(std::string(where) + " nvCOMP status=" + std::to_string(static_cast<int>(status)));
}

static std::string rank_path(std::string templ, int rank)
{
  const std::string needle = "{rank}";
  const std::string repl = std::to_string(rank);
  std::size_t pos = templ.find(needle);
  while (pos != std::string::npos) {
    templ.replace(pos, needle.size(), repl);
    pos = templ.find(needle, pos + repl.size());
  }
  return templ;
}

static int mpi_count(std::uint64_t size)
{
  if (size > static_cast<std::uint64_t>(std::numeric_limits<int>::max()))
    throw std::runtime_error("MPI byte count exceeds INT_MAX; lower --chunk-amps for this toy benchmark");
  return static_cast<int>(size);
}

static std::uint64_t lcg_next(std::uint64_t& state)
{
  state = state * 6364136223846793005ULL + 1442695040888963407ULL;
  return state;
}

static double unit_from_u64(std::uint64_t value)
{
  return static_cast<double>(value >> 11) * (1.0 / 9007199254740992.0);
}

static std::vector<std::uint8_t> bytes_from_complex(const std::vector<Complex64>& values)
{
  std::vector<std::uint8_t> bytes(values.size() * sizeof(Complex64));
  std::memcpy(bytes.data(), values.data(), bytes.size());
  return bytes;
}

static std::vector<std::uint8_t> generate_synthetic(const Options& opts, int rank)
{
  std::vector<Complex64> values(opts.payload_amps);
  const double scale = opts.payload_amps > 0 ? 1.0 / std::sqrt(static_cast<double>(opts.payload_amps)) : 1.0;

  if (opts.pattern == "zero_sparse") {
    for (std::size_t i = 0; i < values.size(); i++) {
      values[i].real = 0.0;
      values[i].imag = 0.0;
      if ((i % 1024) == 0)
        values[i].real = scale * static_cast<double>(rank + 1);
    }
  } else if (opts.pattern == "h_halfzero_real") {
    for (std::size_t i = 0; i < values.size(); i++) {
      const bool nonzero = ((i / 64) % 2) == 0;
      values[i].real = nonzero ? scale : 0.0;
      values[i].imag = 0.0;
    }
  } else if (opts.pattern == "phase_lattice") {
    for (std::size_t i = 0; i < values.size(); i++) {
      const double angle = (static_cast<double>((i + static_cast<std::size_t>(rank)) % 16) * M_PI) / 8.0;
      values[i].real = scale * std::cos(angle);
      values[i].imag = scale * std::sin(angle);
    }
  } else if (opts.pattern == "random_mantissa_normed") {
    std::uint64_t rng = 20260402ULL + static_cast<std::uint64_t>(rank) * 1315423911ULL;
    const double random_scale = scale / std::sqrt(2.0);
    for (std::size_t i = 0; i < values.size(); i++) {
      values[i].real = (2.0 * unit_from_u64(lcg_next(rng)) - 1.0) * random_scale;
      values[i].imag = (2.0 * unit_from_u64(lcg_next(rng)) - 1.0) * random_scale;
    }
  } else {
    throw std::runtime_error("unsupported synthetic pattern " + opts.pattern);
  }

  return bytes_from_complex(values);
}

static std::vector<std::uint8_t> read_file_prefix(const std::string& path, std::size_t requested_bytes)
{
  std::ifstream in(path, std::ios::binary);
  if (!in)
    throw std::runtime_error("cannot open payload " + path);
  in.seekg(0, std::ios::end);
  const std::size_t file_size = static_cast<std::size_t>(in.tellg());
  in.seekg(0, std::ios::beg);
  const std::size_t bytes = requested_bytes == 0 ? file_size : std::min(requested_bytes, file_size);
  std::vector<std::uint8_t> data(bytes);
  in.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(bytes));
  if (static_cast<std::size_t>(in.gcount()) != bytes)
    throw std::runtime_error("short read from payload " + path);
  return data;
}

static std::vector<std::uint8_t> load_payload(const Options& opts, int rank)
{
  if (opts.source_kind == "synthetic")
    return generate_synthetic(opts, rank);
  const std::size_t requested = opts.payload_amps == 0 ? 0 : opts.payload_amps * sizeof(Complex64);
  return read_file_prefix(rank_path(opts.payload_template, rank), requested);
}

static std::unique_ptr<nvcomp::nvcompManagerBase> make_manager(const std::string& codec, std::size_t chunk_bytes, cudaStream_t stream)
{
  if (codec == "nvcomp_lz4") {
#if QUEST_COMPRESSION_HAVE_NVCOMP_LZ4
    return std::make_unique<nvcomp::LZ4Manager>(
      chunk_bytes, nvcompBatchedLZ4CompressDefaultOpts, nvcompBatchedLZ4DecompressDefaultOpts,
      stream, nvcomp::NoComputeNoVerify, nvcomp::BitstreamKind::NVCOMP_NATIVE);
#else
    return nullptr;
#endif
  }
  if (codec == "nvcomp_gdeflate") {
#if QUEST_COMPRESSION_HAVE_NVCOMP_GDEFLATE
    return std::make_unique<nvcomp::GdeflateManager>(
      chunk_bytes, nvcompBatchedGdeflateCompressDefaultOpts, nvcompBatchedGdeflateDecompressDefaultOpts,
      stream, nvcomp::NoComputeNoVerify, nvcomp::BitstreamKind::NVCOMP_NATIVE);
#else
    return nullptr;
#endif
  }
  if (codec == "nvcomp_bitcomp") {
#if QUEST_COMPRESSION_HAVE_NVCOMP_BITCOMP
    auto compress_opts = nvcompBatchedBitcompCompressDefaultOpts;
#ifdef NVCOMP_TYPE_DOUBLE
    compress_opts.data_type = NVCOMP_TYPE_DOUBLE;
#else
    compress_opts.data_type = NVCOMP_TYPE_ULONGLONG;
#endif
    return std::make_unique<nvcomp::BitcompManager>(
      chunk_bytes, compress_opts, nvcompBatchedBitcompDecompressDefaultOpts,
      stream, nvcomp::NoComputeNoVerify, nvcomp::BitstreamKind::NVCOMP_NATIVE);
#else
    return nullptr;
#endif
  }
  throw std::runtime_error("unsupported codec " + codec);
}

static double wall()
{
  return MPI_Wtime();
}

static void mpi_exchange_bytes(const void* send, std::uint64_t send_size, void* recv, std::uint64_t recv_size, int pair_rank)
{
  MPI_Sendrecv(
    send, mpi_count(send_size), MPI_BYTE, pair_rank, 0,
    recv, mpi_count(recv_size), MPI_BYTE, pair_rank, 0,
    MPI_COMM_WORLD, MPI_STATUS_IGNORE);
}

static Timing run_raw_rep(
  const Options& opts,
  const std::vector<std::uint8_t>& local,
  const std::vector<std::uint8_t>& peer_expected,
  int pair_rank,
  std::uint8_t* d_src,
  std::uint8_t* d_recv,
  std::uint8_t* h_raw_send,
  std::uint8_t* h_raw_recv,
  cudaStream_t stream)
{
  Timing t;
  const std::size_t payload_bytes = local.size();
  const std::size_t chunk_bytes = opts.chunk_amps == 0 ? payload_bytes : opts.chunk_amps * sizeof(Complex64);
  double start_total = wall();

  {
    ScopedNvtxRange exchange_range("quest_compression.exchange.raw");
    for (std::size_t offset = 0; offset < payload_bytes; offset += chunk_bytes) {
      const std::size_t bytes = std::min(chunk_bytes, payload_bytes - offset);
      double s = wall();
      {
        ScopedNvtxRange stage_range("quest_compression.stage.d2h");
        CUDA_CHECK(cudaMemcpyAsync(h_raw_send, d_src + offset, bytes, cudaMemcpyDeviceToHost, stream));
        CUDA_CHECK(cudaStreamSynchronize(stream));
      }
      t.d2h += wall() - s;

      s = wall();
      {
        ScopedNvtxRange stage_range("quest_compression.stage.mpi");
        mpi_exchange_bytes(h_raw_send, bytes, h_raw_recv, bytes, pair_rank);
      }
      t.mpi += wall() - s;

      s = wall();
      {
        ScopedNvtxRange stage_range("quest_compression.stage.h2d");
        CUDA_CHECK(cudaMemcpyAsync(d_recv + offset, h_raw_recv, bytes, cudaMemcpyHostToDevice, stream));
        CUDA_CHECK(cudaStreamSynchronize(stream));
      }
      t.h2d += wall() - s;
      t.compressed_bytes += bytes;
    }
  }

  t.total = wall() - start_total;
  std::vector<std::uint8_t> received(payload_bytes);
  CUDA_CHECK(cudaMemcpy(received.data(), d_recv, payload_bytes, cudaMemcpyDeviceToHost));
  if (received != peer_expected)
    t.verify_status = "FAIL";
  return t;
}

static Timing run_compressed_rep(
  const Options& opts,
  const std::vector<std::uint8_t>& local,
  const std::vector<std::uint8_t>& peer_expected,
  int pair_rank,
  std::uint8_t* d_src,
  std::uint8_t* d_recv,
  std::uint8_t* h_raw_send,
  std::uint8_t* h_raw_recv,
  std::uint8_t* d_comp_send,
  std::uint8_t* d_comp_recv,
  std::uint8_t* h_comp_send,
  std::uint8_t* h_comp_recv,
  size_t* d_comp_size,
  nvcomp::nvcompManagerBase& manager,
  cudaStream_t stream)
{
  Timing t;
  const std::size_t payload_bytes = local.size();
  const std::size_t chunk_bytes = opts.chunk_amps == 0 ? payload_bytes : opts.chunk_amps * sizeof(Complex64);
  double start_total = wall();

  {
    ScopedNvtxRange exchange_range("quest_compression.exchange.compressed");
    for (std::size_t offset = 0; offset < payload_bytes; offset += chunk_bytes) {
      const std::size_t bytes = std::min(chunk_bytes, payload_bytes - offset);
      std::uint64_t local_comp_size = 0;
      std::uint64_t peer_comp_size = 0;

      double s = wall();
      {
        ScopedNvtxRange stage_range("quest_compression.stage.compress");
        auto comp_config = manager.configure_compression(bytes);
        manager.compress(d_src + offset, d_comp_send, comp_config, d_comp_size);
        CUDA_CHECK(cudaStreamSynchronize(stream));
        if (comp_config.get_status() != nullptr)
          check_nvcomp(*comp_config.get_status(), "compress");
        CUDA_CHECK(cudaMemcpy(&local_comp_size, d_comp_size, sizeof(size_t), cudaMemcpyDeviceToHost));
      }
      t.compress += wall() - s;

      s = wall();
      {
        ScopedNvtxRange stage_range("quest_compression.stage.size_exchange");
        MPI_Sendrecv(
          &local_comp_size, 1, MPI_UINT64_T, pair_rank, 1,
          &peer_comp_size, 1, MPI_UINT64_T, pair_rank, 1,
          MPI_COMM_WORLD, MPI_STATUS_IGNORE);
      }
      t.size_exchange += wall() - s;

      if (local_comp_size >= bytes || peer_comp_size >= bytes) {
        ScopedNvtxRange fallback_range("quest_compression.exchange.fallback_raw");
        t.fallback_used = 1;
        s = wall();
        {
          ScopedNvtxRange stage_range("quest_compression.stage.d2h");
          CUDA_CHECK(cudaMemcpyAsync(h_raw_send, d_src + offset, bytes, cudaMemcpyDeviceToHost, stream));
          CUDA_CHECK(cudaStreamSynchronize(stream));
        }
        t.d2h += wall() - s;

        s = wall();
        {
          ScopedNvtxRange stage_range("quest_compression.stage.mpi");
          mpi_exchange_bytes(h_raw_send, bytes, h_raw_recv, bytes, pair_rank);
        }
        t.mpi += wall() - s;

        s = wall();
        {
          ScopedNvtxRange stage_range("quest_compression.stage.h2d");
          CUDA_CHECK(cudaMemcpyAsync(d_recv + offset, h_raw_recv, bytes, cudaMemcpyHostToDevice, stream));
          CUDA_CHECK(cudaStreamSynchronize(stream));
        }
        t.h2d += wall() - s;
        t.compressed_bytes += bytes;
        continue;
      }

      s = wall();
      {
        ScopedNvtxRange stage_range("quest_compression.stage.d2h");
        CUDA_CHECK(cudaMemcpyAsync(h_comp_send, d_comp_send, local_comp_size, cudaMemcpyDeviceToHost, stream));
        CUDA_CHECK(cudaStreamSynchronize(stream));
      }
      t.d2h += wall() - s;

      s = wall();
      {
        ScopedNvtxRange stage_range("quest_compression.stage.mpi");
        mpi_exchange_bytes(h_comp_send, local_comp_size, h_comp_recv, peer_comp_size, pair_rank);
      }
      t.mpi += wall() - s;

      s = wall();
      {
        ScopedNvtxRange stage_range("quest_compression.stage.h2d");
        CUDA_CHECK(cudaMemcpyAsync(d_comp_recv, h_comp_recv, peer_comp_size, cudaMemcpyHostToDevice, stream));
        CUDA_CHECK(cudaStreamSynchronize(stream));
      }
      t.h2d += wall() - s;

      s = wall();
      {
        ScopedNvtxRange stage_range("quest_compression.stage.decompress");
        auto decomp_config = manager.configure_decompression(d_comp_recv);
        manager.decompress(d_recv + offset, d_comp_recv, decomp_config);
        CUDA_CHECK(cudaStreamSynchronize(stream));
        if (decomp_config.get_status() != nullptr)
          check_nvcomp(*decomp_config.get_status(), "decompress");
      }
      t.decompress += wall() - s;
      t.compressed_bytes += local_comp_size;
    }
  }

  t.total = wall() - start_total;
  std::vector<std::uint8_t> received(payload_bytes);
  CUDA_CHECK(cudaMemcpy(received.data(), d_recv, payload_bytes, cudaMemcpyDeviceToHost));
  if (received != peer_expected)
    t.verify_status = "FAIL";
  return t;
}

static bool file_has_content(const std::string& path)
{
  struct stat st;
  return stat(path.c_str(), &st) == 0 && st.st_size > 0;
}

static void write_header(FILE* out)
{
  std::fprintf(out,
    "source_kind\tpattern\tcheckpoint\texchange_shape\tcodec\tpayload_bytes\tcompressed_bytes\tcompression_ratio\t"
    "d2h_s\tcompress_s\tsize_exchange_s\tmpi_s\th2d_s\tdecompress_s\ttotal_s\tspeedup_vs_raw\tfallback_used\t"
    "verify_status\tstatus\tallocation_id\trank\tpair_rank\trep\twarmup\n");
}

static void write_row(FILE* out, const Options& opts, const Timing& t, std::size_t payload_bytes, int rank, int pair_rank, int rep, int warmup)
{
  const double ratio = t.compressed_bytes > 0 ? static_cast<double>(payload_bytes) / static_cast<double>(t.compressed_bytes) : 0.0;
  std::fprintf(out,
    "%s\t%s\t%s\t%s\t%s\t%zu\t%llu\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t\t%d\t%s\t%s\t%s\t%d\t%d\t%d\t%d\n",
    opts.source_kind.c_str(), opts.pattern.c_str(), opts.checkpoint.c_str(), opts.exchange_shape.c_str(),
    opts.codec.c_str(), payload_bytes, static_cast<unsigned long long>(t.compressed_bytes), ratio,
    t.d2h, t.compress, t.size_exchange, t.mpi, t.h2d, t.decompress, t.total, t.fallback_used,
    t.verify_status.c_str(), t.status.c_str(), opts.allocation_id.c_str(), rank, pair_rank, rep, warmup);
}

int main(int argc, char** argv)
{
  MPI_Init(&argc, &argv);
  int rank = 0;
  int ranks = 1;
  MPI_Comm_rank(MPI_COMM_WORLD, &rank);
  MPI_Comm_size(MPI_COMM_WORLD, &ranks);

  try {
    Options opts = parse_options(argc, argv);
    if ((ranks % 2) != 0)
      throw std::runtime_error("compression_exchange requires an even number of ranks");
    const int pair_rank = rank ^ 1;

    int device_count = 0;
    CUDA_CHECK(cudaGetDeviceCount(&device_count));
    if (device_count <= 0)
      throw std::runtime_error("no CUDA devices visible");
    CUDA_CHECK(cudaSetDevice(rank % device_count));

    std::vector<std::uint8_t> local = load_payload(opts, rank);
    std::vector<std::uint8_t> peer_expected = load_payload(opts, pair_rank);
    if (local.empty() || (local.size() % sizeof(Complex64)) != 0)
      throw std::runtime_error("payload is empty or not FP64 complex aligned");
    if (local.size() != peer_expected.size())
      throw std::runtime_error("paired ranks must use equal payload sizes");

    std::uint64_t local_size = static_cast<std::uint64_t>(local.size());
    std::uint64_t peer_size = 0;
    MPI_Sendrecv(&local_size, 1, MPI_UINT64_T, pair_rank, 2, &peer_size, 1, MPI_UINT64_T, pair_rank, 2, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    if (local_size != peer_size)
      throw std::runtime_error("MPI pair payload size mismatch");

    cudaStream_t stream;
    CUDA_CHECK(cudaStreamCreate(&stream));

    std::uint8_t* d_src = nullptr;
    std::uint8_t* d_recv = nullptr;
    std::uint8_t* h_raw_send = nullptr;
    std::uint8_t* h_raw_recv = nullptr;
    CUDA_CHECK(cudaMalloc(&d_src, local.size()));
    CUDA_CHECK(cudaMalloc(&d_recv, local.size()));
    CUDA_CHECK(cudaMallocHost(&h_raw_send, local.size()));
    CUDA_CHECK(cudaMallocHost(&h_raw_recv, local.size()));
    CUDA_CHECK(cudaMemcpy(d_src, local.data(), local.size(), cudaMemcpyHostToDevice));

    std::unique_ptr<nvcomp::nvcompManagerBase> manager;
    std::uint8_t* d_comp_send = nullptr;
    std::uint8_t* d_comp_recv = nullptr;
    std::uint8_t* h_comp_send = nullptr;
    std::uint8_t* h_comp_recv = nullptr;
    size_t* d_comp_size = nullptr;
    std::size_t max_comp_bytes = 0;
    const std::size_t logical_chunk_bytes = opts.chunk_amps == 0 ? local.size() : opts.chunk_amps * sizeof(Complex64);
    const std::size_t max_chunk_bytes = std::min(logical_chunk_bytes, local.size());

    if (opts.codec != "raw") {
      manager = make_manager(opts.codec, opts.nvcomp_chunk_bytes, stream);
      if (!manager) {
        if (rank == 0)
          std::fprintf(stderr, "SKIPPED_DEPENDENCY_MISSING: %s\n", opts.codec.c_str());
        MPI_Finalize();
        return EXIT_SUCCESS;
      }
      auto max_config = manager->configure_compression(max_chunk_bytes);
      max_comp_bytes = max_config.max_compressed_buffer_size;
      CUDA_CHECK(cudaMalloc(&d_comp_send, max_comp_bytes));
      CUDA_CHECK(cudaMalloc(&d_comp_recv, max_comp_bytes));
      CUDA_CHECK(cudaMallocHost(&h_comp_send, max_comp_bytes));
      CUDA_CHECK(cudaMallocHost(&h_comp_recv, max_comp_bytes));
      CUDA_CHECK(cudaMalloc(&d_comp_size, sizeof(size_t)));
    }

    FILE* out = stdout;
    bool should_close = false;
    const std::string output_path = rank_path(opts.output, rank);
    if (output_path != "-") {
      bool write_header_now = !file_has_content(output_path);
      out = std::fopen(output_path.c_str(), "a");
      if (!out)
        throw std::runtime_error("cannot open output " + output_path);
      should_close = true;
      if (write_header_now)
        write_header(out);
    } else if (rank == 0) {
      write_header(out);
    }

    for (int rep = 0; rep < opts.warmup + opts.reps; rep++) {
      Timing timing;
      const int is_warmup = rep < opts.warmup ? 1 : 0;
      CUDA_CHECK(cudaMemset(d_recv, 0, local.size()));
      MPI_Barrier(MPI_COMM_WORLD);
      if (opts.codec == "raw") {
        timing = run_raw_rep(opts, local, peer_expected, pair_rank, d_src, d_recv, h_raw_send, h_raw_recv, stream);
      } else {
        timing = run_compressed_rep(opts, local, peer_expected, pair_rank, d_src, d_recv, h_raw_send, h_raw_recv,
          d_comp_send, d_comp_recv, h_comp_send, h_comp_recv, d_comp_size, *manager, stream);
      }
      if (!is_warmup)
        write_row(out, opts, timing, local.size(), rank, pair_rank, rep, is_warmup);
      std::fflush(out);
    }

    if (should_close)
      std::fclose(out);
    if (d_comp_size) cudaFree(d_comp_size);
    if (h_comp_recv) cudaFreeHost(h_comp_recv);
    if (h_comp_send) cudaFreeHost(h_comp_send);
    if (d_comp_recv) cudaFree(d_comp_recv);
    if (d_comp_send) cudaFree(d_comp_send);
    cudaFreeHost(h_raw_recv);
    cudaFreeHost(h_raw_send);
    cudaFree(d_recv);
    cudaFree(d_src);
    cudaStreamDestroy(stream);
    MPI_Finalize();
    return EXIT_SUCCESS;
  } catch (const std::exception& e) {
    std::fprintf(stderr, "rank %d ERROR: %s\n", rank, e.what());
    MPI_Abort(MPI_COMM_WORLD, 1);
    return EXIT_FAILURE;
  }
}
