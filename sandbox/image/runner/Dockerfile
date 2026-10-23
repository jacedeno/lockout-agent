# Analysis runtime for sandboxed jobs. No network at run time: everything a job may import
# is installed here, at snapshot build time.
FROM docker.io/library/python:3.12-slim

RUN pip install --no-cache-dir \
        numpy==2.1.3 \
        scipy==1.14.1 \
        pandas==2.2.3 \
        matplotlib==3.9.2

# GPG_KEY comes from the base image (Python release signing key ID); blank it so a job's
# environment carries nothing named like a credential.
ENV GPG_KEY= \
    MPLCONFIGDIR=/tmp \
    MPLBACKEND=Agg \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /work
ENTRYPOINT ["python3", "-I"]
