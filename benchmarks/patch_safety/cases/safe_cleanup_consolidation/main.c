#include <stdlib.h>

int process(char *p, int fail) {
    if (!p)
        return -1;
    if (fail) {
        free(p);
        return -2;
    }
    free(p);
    return 0;
}
