import time
for i in range(5):
    print("child line", i, round(time.time(), 2), flush=True)
    time.sleep(1.2)
