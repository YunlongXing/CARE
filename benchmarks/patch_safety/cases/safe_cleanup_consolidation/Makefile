CC ?= cc
CFLAGS ?= -Wall -Wextra -Werror -std=c11

all: test_patch

test_patch: main.c test.c
	$(CC) $(CFLAGS) main.c test.c -o $@

test: test_patch
	./test_patch
