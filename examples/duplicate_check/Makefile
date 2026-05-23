CC ?= cc
CFLAGS ?= -std=c99 -Wall -Wextra -Werror -g

.PHONY: all test clean

all: test_duplicate_check

test_duplicate_check: foo.c test_foo.c foo.h
	$(CC) $(CFLAGS) foo.c test_foo.c -o test_duplicate_check

test: test_duplicate_check
	./test_duplicate_check

clean:
	rm -f test_duplicate_check
