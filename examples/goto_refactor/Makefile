CC ?= cc
CFLAGS ?= -std=c99 -Wall -Wextra -Werror -g

.PHONY: all test clean

all: test_goto

test_goto: process.c test_process.c process.h
	$(CC) $(CFLAGS) process.c test_process.c -o test_goto

test: test_goto
	./test_goto

clean:
	rm -f test_goto
