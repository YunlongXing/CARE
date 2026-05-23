# Goto Cleanup Refactoring Example

This example intentionally repeats cleanup across three return paths:

- `free(p)` when `p` is non-null.
- `lock_release(&lock)` before every return.
- Different return values for each path.

CARE should detect the repeated manual cleanup and suggest a shared cleanup path:

```c
int ret = 0;

if (err1) {
    ret = -1;
    goto out;
}

if (err2) {
    do_work();
    ret = -2;
    goto out;
}

do_work();

out:
    if (p)
        free(p);
    lock_release(&lock);
    return ret;
```

Run:

```bash
make test
```
