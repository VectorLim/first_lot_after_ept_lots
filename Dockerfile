ARG SOIMS_BASE_IMAGE=amr-registry.caas.intel.com/soia/icmpcs-soims:base
FROM ${SOIMS_BASE_IMAGE}
USER root
WORKDIR /app
ENV ENV_MODE=test
ENV NO_PROXY=localhost,127.0.0.1,.atmedispo.intel.com
ENV no_proxy=localhost,127.0.0.1,.atmedispo.intel.com
ENV PYTHONPATH=/app:/app/dependencies
ENV FUNCCONF=/etc/icmpcs/conf
ENV ENVFUNCAPPLOC=/app
COPY . /app
RUN python -m pip install -r dependencies/requirements.txt \
    && printf 'LOG_LEVEL=INFO\n' > /app/.env \
    && chmod -R a+rX /app \
    && mkdir -p /app/output \
    && chmod 1777 /app/output