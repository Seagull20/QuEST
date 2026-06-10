#ifndef QUEST_PROFILING_HPP
#define QUEST_PROFILING_HPP

extern "C" void quest_profile_range_push(const char* name);
extern "C" void quest_profile_range_pop(void);

class QuestProfileRange {
  public:
    explicit QuestProfileRange(const char* name) {
        quest_profile_range_push(name);
    }

    ~QuestProfileRange() {
        quest_profile_range_pop();
    }

    QuestProfileRange(const QuestProfileRange&) = delete;
    QuestProfileRange& operator=(const QuestProfileRange&) = delete;
};

#endif
