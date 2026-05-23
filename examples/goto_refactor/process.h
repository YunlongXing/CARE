#ifndef CARE_GOTO_REFACTOR_PROCESS_H
#define CARE_GOTO_REFACTOR_PROCESS_H

extern int care_goto_work_count;
extern int care_goto_lock_acquire_count;
extern int care_goto_lock_release_count;

void reset_process_state(void);
int process(char *p, int err1, int err2);

#endif
