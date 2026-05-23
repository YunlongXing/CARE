#include "foo.h"

int foo(char *p, int len) {
    if (!p)
        return -1;

    if (len <= 0)
        return -2;

    if (!p)
        return -1;

    return p[0] + len;
}
