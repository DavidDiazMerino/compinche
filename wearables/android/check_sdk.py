"""Compile the adapter against actual pinned DAT artifacts, without Android SDK.

Downloads compiler and API jars from Maven Central into /tmp, not the repository.
This verifies API/type compatibility; it does not run an Android device or APK.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile


CACHE = Path(tempfile.gettempdir()) / "compinche-dat-compile"
CENTRAL = "https://repo.maven.apache.org/maven2/"
DEPENDENCIES = [
    ("org.jetbrains.kotlin", "kotlin-compiler-embeddable", "2.2.20", "jar"),
    ("org.jetbrains.kotlin", "kotlin-stdlib", "2.2.20", "jar"),
    ("org.jetbrains.kotlin", "kotlin-script-runtime", "2.2.20", "jar"),
    ("org.jetbrains.kotlin", "kotlin-reflect", "1.6.10", "jar"),
    ("org.jetbrains.kotlin", "kotlin-daemon-embeddable", "2.2.20", "jar"),
    ("org.jetbrains.kotlinx", "kotlinx-coroutines-core-jvm", "1.10.2", "jar"),
    ("org.jetbrains", "annotations", "13.0", "jar"),
    ("com.google.android", "android", "4.1.1.4", "jar"),
    ("com.meta.wearable", "mwdat-core", "1.0.1", "aar"),
    ("com.meta.wearable", "mwdat-camera", "1.0.1", "aar"),
    ("com.meta.wearable", "mwdat-mockdevice", "1.0.1", "aar"),
]


def download(item):
    group, name, version, extension = item
    path = CACHE / f"{name}-{version}.{extension}"
    if not path.exists():
        url = CENTRAL + f"{group.replace('.', '/')}/{name}/{version}/{path.name}"
        temporary = path.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        temporary.replace(path)
    if extension == "aar":
        extracted = path.with_suffix(".jar")
        with zipfile.ZipFile(path) as archive:
            extracted.write_bytes(archive.read("classes.jar"))
        return extracted
    return path


def main():
    CACHE.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        files = list(pool.map(download, DEPENDENCIES))
    classpath = ":".join(str(path) for path in files)
    subprocess.run(["java", "-cp", classpath, "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
        "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath", classpath,
        "-d", str(CACHE / "compinche-replay.jar"), str(Path(__file__).with_name("CompincheReplay.kt"))], check=True)
    print("CompincheReplay.kt compiled against Meta DAT 1.0.1. Android runtime not exercised.")


if __name__ == "__main__":
    main()
