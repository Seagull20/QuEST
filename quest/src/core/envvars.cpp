/** @file
 * Functions for loading environment variables, useful for
 * configuring QuEST ahead of calling initQuESTEnv(), after
 * compilation.
 * 
 * @author Tyson Jones
 */

#include "quest/include/precision.h"
#include "quest/include/types.h"

#ifdef QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE
#include "quest/src/core/envvars.hpp"
#endif

#include "quest/src/core/errors.hpp"
#include "quest/src/core/parser.hpp"
#include "quest/src/core/validation.hpp"

#include <string>
#include <cstdlib>

#ifdef QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE
#include <cstddef>
#endif

using std::string;



/*
 * FIXED ENV-VAR NAMES
 */


namespace envvar_names {
    string PERMIT_NODES_TO_SHARE_GPU = "PERMIT_NODES_TO_SHARE_GPU";
    string DEFAULT_VALIDATION_EPSILON = "DEFAULT_VALIDATION_EPSILON";

    #ifdef QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE
    string GPU_STAGING_MODE = "QUEST_GPU_STAGING_MODE";
    string GPU_STAGING_TILE_MB = "QUEST_GPU_STAGING_TILE_MB";
    string GPU_STAGING_SLOTS = "QUEST_GPU_STAGING_SLOTS";
    string GPU_STAGING_PINNED = "QUEST_GPU_STAGING_PINNED";
    string GPU_STAGING_MPI_PROGRESS = "QUEST_GPU_STAGING_MPI_PROGRESS";
    string FORCE_CPU_STAGING = "QUEST_FORCE_CPU_STAGING";
    #endif
}



/*
 * USER-OVERRIDABLE DEFAULT ENV-VAR VALUES
 */


namespace envvar_values {

    // by default, do not permit GPU sharing since it sabotages performance
    // and should only ever be carefully, deliberately enabled
    bool PERMIT_NODES_TO_SHARE_GPU = false;

    // by default, the initial validation epsilon (before being overriden
    // by users at runtime) should depend on qreal (i.e. FLOAT_PRECISION)
    qreal DEFAULT_VALIDATION_EPSILON = UNSPECIFIED_DEFAULT_VALIDATION_EPSILON;

    #ifdef QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE
    constexpr std::size_t BYTES_PER_MIB = 1024 * 1024;

    // Conservative experimental defaults from the staging-pipeline design.
    GpuStagingConfig GPU_STAGING = {
        GpuStagingMode::RAW,
        64 * BYTES_PER_MIB,
        3,
        true,
        GpuStagingMpiProgress::TESTSOME,
        false
    };
    #endif
}


// indicates whether envvars_validateAndLoadEnvVars() has been called
bool global_areEnvVarsLoaded = false;



/*
 * PRIVATE UTILITIES
 */


bool isEnvVarSpecified(string name) {

    // note var="" is considered unspecified, but var=" " is specified
    const char* ptr = std::getenv(name.c_str());
    return (ptr != nullptr) && (ptr[0] != '\0');
}


string getSpecifiedEnvVarValue(string name) {

    // assumes isEnvVarSpecified returned true
    // (calling getenv() a second time is fine)
    return std::string(std::getenv(name.c_str()));
}


void assertEnvVarsAreLoaded() {

    if (!global_areEnvVarsLoaded)
        error_envVarsNotYetLoaded();
}



/*
 * PRIVATE BESPOKE ENV-VAR LOADERS
 *
 * which we have opted to not-yet make generic 
 * (e.g. for each type) since YAGNI
 */


void validateAndSetWhetherGpuSharingIsPermitted(const char* caller) {

    // permit unspecified, falling back to default value
    string name = envvar_names::PERMIT_NODES_TO_SHARE_GPU;
    if (!isEnvVarSpecified(name))
        return;

    // otherwise ensure value == '0' or '1' precisely (no whitespace)
    string value = getSpecifiedEnvVarValue(name);
    validate_envVarPermitNodesToShareGpu(value, caller);

    // overwrite default env-var value
    envvar_values::PERMIT_NODES_TO_SHARE_GPU = (value[0] == '1');
}


void validateAndSetDefaultValidationEpsilon(const char* caller) {

    // permit unspecified, falling back to the hardcoded precision-specific default
    string name = envvar_names::DEFAULT_VALIDATION_EPSILON;
    if (!isEnvVarSpecified(name))
        return;
    
    // otherwise, validate user passed a positive real integer (or zero)
    string value = getSpecifiedEnvVarValue(name);
    validate_envVarDefaultValidationEpsilon(value, caller);

    // overwrite default env-var value
    envvar_values::DEFAULT_VALIDATION_EPSILON = parser_parseReal(value);    
}


#ifdef QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE

void validateAndSetGpuStagingMode(const char* caller) {

    string name = envvar_names::GPU_STAGING_MODE;
    if (!isEnvVarSpecified(name))
        return;

    string value = getSpecifiedEnvVarValue(name);
    validate_envVarGpuStagingMode(value, caller);

    if (value == "raw")
        envvar_values::GPU_STAGING.mode = GpuStagingMode::RAW;
    else if (value == "bulk_async")
        envvar_values::GPU_STAGING.mode = GpuStagingMode::BULK_ASYNC;
    else if (value == "tiled_materialize")
        envvar_values::GPU_STAGING.mode = GpuStagingMode::TILED_MATERIALIZE;
    else
        envvar_values::GPU_STAGING.mode = GpuStagingMode::TILED_FUSED;
}


void validateAndSetGpuStagingTileSize(const char* caller) {

    string name = envvar_names::GPU_STAGING_TILE_MB;
    if (!isEnvVarSpecified(name))
        return;

    string value = getSpecifiedEnvVarValue(name);
    validate_envVarGpuStagingTileMb(value, caller);

    std::size_t tileMib = static_cast<std::size_t>(std::stoull(value));
    envvar_values::GPU_STAGING.tileBytes = tileMib * envvar_values::BYTES_PER_MIB;
}


void validateAndSetGpuStagingSlots(const char* caller) {

    string name = envvar_names::GPU_STAGING_SLOTS;
    if (!isEnvVarSpecified(name))
        return;

    string value = getSpecifiedEnvVarValue(name);
    validate_envVarGpuStagingSlots(value, caller);

    envvar_values::GPU_STAGING.slots = std::stoi(value);
}


void validateAndSetGpuStagingPinned(const char* caller) {

    string name = envvar_names::GPU_STAGING_PINNED;
    if (!isEnvVarSpecified(name))
        return;

    string value = getSpecifiedEnvVarValue(name);
    validate_envVarGpuStagingPinned(value, caller);

    envvar_values::GPU_STAGING.usePinnedMemory = (value[0] == '1');
}


void validateAndSetGpuStagingMpiProgress(const char* caller) {

    string name = envvar_names::GPU_STAGING_MPI_PROGRESS;
    if (!isEnvVarSpecified(name))
        return;

    string value = getSpecifiedEnvVarValue(name);
    validate_envVarGpuStagingMpiProgress(value, caller);

    if (value == "wait")
        envvar_values::GPU_STAGING.mpiProgress = GpuStagingMpiProgress::WAIT;
    else if (value == "testsome")
        envvar_values::GPU_STAGING.mpiProgress = GpuStagingMpiProgress::TESTSOME;
    else
        envvar_values::GPU_STAGING.mpiProgress = GpuStagingMpiProgress::TESTANY;
}


void validateAndSetForceCpuStaging(const char* caller) {

    string name = envvar_names::FORCE_CPU_STAGING;
    if (!isEnvVarSpecified(name))
        return;

    string value = getSpecifiedEnvVarValue(name);
    validate_envVarForceCpuStaging(value, caller);

    envvar_values::GPU_STAGING.forceCpuStaging = (value[0] == '1');
}

#endif



/*
 * PUBLIC
 */


void envvars_validateAndLoadEnvVars(const char* caller) {

    // error if loaded twice since this indicates spaghetti
    if (global_areEnvVarsLoaded)
        error_envVarsAlreadyLoaded();

    // load all env-vars
    validateAndSetWhetherGpuSharingIsPermitted(caller);
    validateAndSetDefaultValidationEpsilon(caller);

    #ifdef QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE
    validateAndSetGpuStagingMode(caller);
    validateAndSetGpuStagingTileSize(caller);
    validateAndSetGpuStagingSlots(caller);
    validateAndSetGpuStagingPinned(caller);
    validateAndSetGpuStagingMpiProgress(caller);
    validateAndSetForceCpuStaging(caller);
    #endif

    // ensure no re-loading
    global_areEnvVarsLoaded = true;
}


bool envvars_getWhetherGpuSharingIsPermitted() {
    assertEnvVarsAreLoaded();

    return envvar_values::PERMIT_NODES_TO_SHARE_GPU;
}


qreal envvars_getDefaultValidationEpsilon() {
    assertEnvVarsAreLoaded();

    return envvar_values::DEFAULT_VALIDATION_EPSILON;
}


#ifdef QUEST_EXPERIMENTAL_GPU_STAGING_PIPELINE
const GpuStagingConfig& envvars_getGpuStagingConfig() {
    assertEnvVarsAreLoaded();

    return envvar_values::GPU_STAGING;
}
#endif
