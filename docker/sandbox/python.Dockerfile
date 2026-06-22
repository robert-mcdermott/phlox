# syntax=docker/dockerfile:1
#
# Phlox "batteries-included" Python sandbox image.
#
# Used by the CONTAINER sandbox runner as `python_image` so the agent's
# execute_python / run_shell tools start with the common data-analysis stack
# already installed — no per-call `pip install numpy ... matplotlib ...` dance.
#
# Build:   ./docker/sandbox/build.sh            (or: podman build -f docker/sandbox/python.Dockerfile -t phlox-sandbox-python:latest .)
# Wire up: set sandbox.container.python_image: phlox-sandbox-python:latest in backend/config.yml
#
# Once packages are baked in here you can also set sandbox.container.network: none
# for stronger isolation — the agent no longer needs the network to fetch them.

FROM python:3.12-slim

# OS-level tools the agent may want from run_shell, plus fonts so matplotlib can
# render text (slim ships none), and ca-certificates for TLS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git curl wget jq unzip \
        ca-certificates \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Standard data / analysis stack. Versions pinned for reproducible rebuilds — bump
# deliberately and re-run docker/sandbox/build.sh.
# Matplotlib defaults to the non-interactive Agg backend with no display, so plots
# saved to /work (e.g. savefig('plot.png')) are captured as artifacts.
RUN pip install --no-cache-dir \
        numpy==2.5.0 \
        pandas==3.0.3 \
        scipy==1.18.0 \
        matplotlib==3.11.0 \
        seaborn==0.13.2 \
        scikit-learn==1.9.0 \
        statsmodels==0.14.6 \
        openpyxl==3.1.5 \
        XlsxWriter==3.2.9 \
        pypdf==6.13.3 \
        python-docx==1.2.0 \
        pillow==12.2.0 \
        requests==2.34.2 \
        httpx==0.28.1 \
        beautifulsoup4==4.15.0 \
        lxml==6.1.1 \
        sympy==1.14.0 \
        networkx==3.6.1 \
        tabulate==0.10.0

# Headless matplotlib by default; keep Python output unbuffered for live logs.
ENV MPLBACKEND=Agg \
    PYTHONUNBUFFERED=1

WORKDIR /work
