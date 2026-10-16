"""Request idle-only maintenance through the local API; never access HCI directly."""
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

URL = "http://127.0.0.1:8000/api/devices/bluetooth/reset"


def main():
    request = Request(URL, data=json.dumps({"confirmed": True, "idle_only": True}).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=10) as response:
            job = json.load(response)
    except HTTPError as exc:
        if exc.code == 409:
            print("Reset quotidien ignoré : Bluetooth occupé.")
            return
        raise
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with urlopen(URL, timeout=10) as response:
            result = json.load(response)
        if result.get("generation") == job["generation"]:
            if result.get("status") in {"done", "skipped"}:
                print(result["message"])
                return
            if result.get("status") in {"error", "rejected"}:
                raise RuntimeError(result["message"])
        time.sleep(1)
    raise TimeoutError("Le gestionnaire n’a pas confirmé le reset quotidien.")


if __name__ == "__main__":
    main()
