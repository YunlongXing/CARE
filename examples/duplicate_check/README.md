# Duplicate Check Refactoring Example

This example repeats the null check for `p` after a length check:

```c
if (!p)
    return -1;

if (len <= 0)
    return -2;

if (!p)
    return -1;
```

CARE should suggest removing the second `if (!p)` only because there is no
assignment to `p`, mutating call, or obvious aliasing risk between the two
checks:

```c
int foo(char *p, int len) {
    if (!p)
        return -1;

    if (len <= 0)
        return -2;

    return p[0] + len;
}
```

Run:

```bash
make test
```
