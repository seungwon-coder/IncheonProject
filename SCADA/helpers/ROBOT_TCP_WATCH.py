import subprocess
import time
from pathlib import Path


ROBOT_IP = "127.0.0.1"
KEP_PORT = 49320
CHECK_INTERVAL = 3

BASE_DIR = Path(__file__).resolve().parent

STATUS_FILE = BASE_DIR / "ROBOT_STATUS.txt"
TEMP_FILE = BASE_DIR / "ROBOT_STATUS_TMP.txt"


def check_robot_connection():

    command = (
        f"@(Get-NetTCPConnection "
        f"-LocalPort {KEP_PORT} "
        f"-RemoteAddress {ROBOT_IP} "
        f"-State Established "
        f"-ErrorAction SilentlyContinue).Count"
    )

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            command
        ],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW
    )

    try:
        count = int(result.stdout.strip())
    except:
        count = 0

    return count >= 1


def write_status(value):

    TEMP_FILE.write_text(
        str(value),
        encoding="ascii"
    )

    TEMP_FILE.replace(STATUS_FILE)


print("ROBOT 통신 감시 시작")
print("ROBOT PC :", ROBOT_IP)
print("KEP PORT :", KEP_PORT)
print("Ctrl+C = 종료")
print()


try:

    while True:

        connected = check_robot_connection()

        if connected:

            write_status(0)
            print("ROBOT 정상")

        else:

            write_status(1)
            print("ROBOT 통신이상")

        time.sleep(CHECK_INTERVAL)


except KeyboardInterrupt:

    print()
    print("ROBOT 통신 감시 종료")