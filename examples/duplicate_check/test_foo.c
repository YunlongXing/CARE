#include "foo.h"

#include <assert.h>

static void test_null_pointer(void) {
    assert(foo(0, 4) == -1);
}

static void test_invalid_length(void) {
    char value[] = "abc";
    assert(foo(value, 0) == -2);
    assert(foo(value, -3) == -2);
}

static void test_success(void) {
    char value[] = "abc";
    assert(foo(value, 3) == 'a' + 3);
}

int main(void) {
    test_null_pointer();
    test_invalid_length();
    test_success();
    return 0;
}
