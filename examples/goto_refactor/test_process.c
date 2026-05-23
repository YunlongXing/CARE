#include "process.h"

#include <assert.h>
#include <stdlib.h>

static char *owned_buffer(void) {
    char *p = malloc(4);
    assert(p != NULL);
    return p;
}

static void test_err1_path(void) {
    reset_process_state();

    assert(process(owned_buffer(), 1, 0) == -1);
    assert(care_goto_work_count == 0);
    assert(care_goto_lock_acquire_count == 1);
    assert(care_goto_lock_release_count == 1);
}

static void test_err2_path(void) {
    reset_process_state();

    assert(process(owned_buffer(), 0, 1) == -2);
    assert(care_goto_work_count == 1);
    assert(care_goto_lock_acquire_count == 1);
    assert(care_goto_lock_release_count == 1);
}

static void test_success_path(void) {
    reset_process_state();

    assert(process(owned_buffer(), 0, 0) == 0);
    assert(care_goto_work_count == 1);
    assert(care_goto_lock_acquire_count == 1);
    assert(care_goto_lock_release_count == 1);
}

static void test_null_success_path(void) {
    reset_process_state();

    assert(process(NULL, 0, 0) == 0);
    assert(care_goto_work_count == 1);
    assert(care_goto_lock_acquire_count == 1);
    assert(care_goto_lock_release_count == 1);
}

int main(void) {
    test_err1_path();
    test_err2_path();
    test_success_path();
    test_null_success_path();
    return 0;
}
