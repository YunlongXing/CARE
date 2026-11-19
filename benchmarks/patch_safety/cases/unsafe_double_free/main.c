#include <stdlib.h>

int dispose(int fail) {
    char *p = malloc(1);
    if (!p)
        return -1;
    free(p);
    return fail ? 1 : 0;
}
