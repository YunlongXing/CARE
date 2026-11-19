int allocate_and_release(int fail);

int main(void) {
    return allocate_and_release(0) == 0 ? 0 : 1;
}
