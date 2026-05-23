#include "process.h"

#include <stdlib.h>

struct lock {
    int held;
};

int care_goto_work_count;
int care_goto_lock_acquire_count;
int care_goto_lock_release_count;

void reset_process_state(void) {
    care_goto_work_count = 0;
    care_goto_lock_acquire_count = 0;
    care_goto_lock_release_count = 0;
}

static void lock_acquire(struct lock *lock) {
    lock->held = 1;
    care_goto_lock_acquire_count++;
}

static void lock_release(struct lock *lock) {
    lock->held = 0;
    care_goto_lock_release_count++;
}

static void do_work(void) {
    care_goto_work_count++;
}

int process(char *p, int err1, int err2) {
    struct lock lock;
    lock_acquire(&lock);

    if (err1) {
        if (p)
            free(p);
        lock_release(&lock);
        return -1;
    }

    if (err2) {
        do_work();
        if (p)
            free(p);
        lock_release(&lock);
        return -2;
    }

    do_work();

    if (p)
        free(p);
    lock_release(&lock);
    return 0;
}
