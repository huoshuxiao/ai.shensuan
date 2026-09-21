# 本机版 local_qlib:latest —— RD-Agent(Q) factor 循环的 qlib 沙箱
#
# 与官方 rdagent/scenarios/qlib/docker/Dockerfile 的差异及理由：
#   1) 基础镜像 python:3.10-slim 而非 pytorch/pytorch:*-cuda*：
#      factor 循环的回测模型是 conf_baseline.yaml 里的 qlib LGBModel
#      （LightGBM，纯 CPU），torch 用不到；且本机 GPU 仅 GTX 965M 2GB，
#      CUDA 版基础镜像 ~7GB 纯属浪费
#   2) 国内代理：docker hub 直连超时，第三方镜像 pytorch/pytorch 不在
#      daocloud/1ms 白名单，library/python 可用
#   3) qlib 走 codeload tarball 到锁定 commit，避免 git clone 全史
#   4) apt/pypi 换 aliyun/tsinghua（直连单请求 ~10s 会拖垮构建）
FROM docker.1ms.run/library/python:3.10-slim

# deb822 源里 URI 已含 /debian 与 /debian-security 路径，故只替换主机名
RUN sed -i "s@//deb.debian.org@//mirrors.aliyun.com@g; s@//security.debian.org@//mirrors.aliyun.com@g" \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && apt-get clean && apt-get update && apt-get install -y --no-install-recommends \
        curl git vim build-essential coreutils ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=120

# qlib 0.9.x 的依赖钳制：numpy<2、pandas 1.x
RUN python -m pip install --upgrade pip "numpy<2" "pandas<2.1" cython

RUN curl -sL -o /tmp/qlib.tar.gz \
        https://codeload.github.com/microsoft/qlib/tar.gz/3e72593b8c985f01979bebcf646658002ac43b00 \
    && mkdir -p /workspace \
    && tar -xzf /tmp/qlib.tar.gz -C /workspace \
    && mv /workspace/qlib-* /workspace/qlib \
    && rm /tmp/qlib.tar.gz

WORKDIR /workspace/qlib

RUN python -m pip install -e .

# cvxpy 不钉版本会装到 1.4+（其 canonicalizer 从 scipy.sparse 导
# eye_array，该符号 scipy 1.14 才有），与 qlib 锁定的 scipy==1.11.4
# 互斥，在 qlib 组合优化策略 import 时抛 ImportError
RUN pip install lightgbm catboost xgboost "scipy==1.11.4" tables "cvxpy<1.4"

# mlflow 3.x 把 file-store tracking 后端判为维护模式并直接抛
# MlflowException，而 qlib 的 workflow（qrun 的 R.start）默认就是
# ./mlruns 目录；容器内看不到宿主机环境变量，只能烤进镜像
ENV MLFLOW_ALLOW_FILE_STORE=true
