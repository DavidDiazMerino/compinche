FROM --platform=linux/amd64 ubuntu:24.04 AS x86runtime
FROM ubuntu:24.04
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    openjdk-21-jdk-headless curl unzip ca-certificates aapt adb qemu-user \
    && rm -rf /var/lib/apt/lists/*
COPY --from=x86runtime /usr/lib/x86_64-linux-gnu/ /opt/x86/usr/lib/x86_64-linux-gnu/
RUN ln -s usr/lib /opt/x86/lib && mkdir /opt/x86/lib64 \
    && ln -s ../usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /opt/x86/lib64/ld-linux-x86-64.so.2
COPY aapt2-arm64-wrapper.sh /opt/aapt2/aapt2
RUN ln -s "$(dirname "$(dirname "$(readlink -f /usr/bin/java)")")" /opt/java
ENV JAVA_HOME=/opt/java
ENV ANDROID_HOME=/android-sdk
ENV GRADLE_USER_HOME=/gradle-cache
WORKDIR /work
