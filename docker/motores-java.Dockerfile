# syntax=docker/dockerfile:1
#
# Imagens dos dois motores Java da Receita (regime regular e Simples Nacional), compiladas a partir dos
# submódulos em engines/. Os repositórios oficiais não publicam Dockerfile.
#
# Um único estágio gera o calculadora-pro.db (flyway do regime regular) e ele é copiado para as duas
# imagens: assim os dois motores usam exatamente a mesma versão de tabela.
#
# Contexto de build: raiz do repositório. Alvos: `regime-regular` e `simples`.

ARG MAVEN_IMAGE=maven:3.9-eclipse-temurin-25
ARG JRE_IMAGE=eclipse-temurin:25-jre

# --- Base de dados compartilhada (SQLite, somente leitura em execução) ----------------------------
FROM ${MAVEN_IMAGE} AS base-dados
WORKDIR /src
COPY engines/calculadora-rtc-regime-regular/ ./
RUN --mount=type=cache,target=/root/.m2 \
    mkdir -p calculadora/db \
 && mvn -B -q flyway:migrate -Dflyway.configFiles=./flyway/flyway-pro.conf --errors \
 && test -s calculadora/db/calculadora-pro.db

# --- Compilação -----------------------------------------------------------------------------------
FROM ${MAVEN_IMAGE} AS build-regime-regular
WORKDIR /src
COPY engines/calculadora-rtc-regime-regular/ ./
# Os testes do motor rodam no CI do projeto oficial; aqui só empacotamos a tag fixada.
RUN --mount=type=cache,target=/root/.m2 mvn -B -q clean package -DskipTests

FROM ${MAVEN_IMAGE} AS build-simples
WORKDIR /src
COPY engines/calculadora-rtc-simples-nacional/ ./
RUN --mount=type=cache,target=/root/.m2 mvn -B -q clean package -DskipTests

# --- Execução ---------------------------------------------------------------------------------------
FROM ${JRE_IMAGE} AS motor-base
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --home-dir /opt/motor motor
# Os JARs abrem ./calculadora/db/calculadora-pro.db relativo ao diretório de trabalho.
WORKDIR /opt/motor
COPY --from=base-dados --chown=root:root /src/calculadora/db/calculadora-pro.db calculadora/db/calculadora-pro.db
# -XX:-UseBASE64Intrinsics: recomendado no CHANGELOG 1.0.1 do Simples (aviso do OpenJDK de 17/11/2026).
ENV JAVA_OPTS="-XX:-UseBASE64Intrinsics -XX:MaxRAMPercentage=75" \
    TZ=America/Sao_Paulo
USER motor
ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -Dspring.profiles.active=offline -jar app.jar"]

FROM motor-base AS regime-regular
COPY --from=build-regime-regular /src/target/api-regime-geral.jar app.jar
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD curl -fsS http://localhost:8080/api/calculadora/dados-abertos/versao > /dev/null || exit 1

FROM motor-base AS simples
COPY --from=build-simples /src/target/api-simples-nacional.jar app.jar
EXPOSE 8082
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD curl -fsS http://localhost:8082/api/versao > /dev/null || exit 1
