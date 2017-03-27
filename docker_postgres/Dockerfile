FROM postgres:9.5
MAINTAINER Jorge Martínez "j.martinezortega@gmail.com"

ENV POSTGIS_MAJOR 2.3
ENV POSTGIS_VERSION 2.3.2+dfsg-1~exp2~145.gita526c31.pgdg16.04+1

RUN apt-get update && \
  apt-get install -y --no-install-recommends \
    postgresql-$PG_MAJOR-postgis-$POSTGIS_MAJOR \
    postgis-$POSTGIS_MAJOR && \
  rm -rf /var/lib/apt/lists/*


RUN mkdir -p /docker-entrypoint-initdb.d &&\
      mkdir -p /data

COPY ./initdb-postgis.sh /docker-entrypoint-initdb.d/postgis.sh

ENTRYPOINT ["/docker-entrypoint.sh"]

EXPOSE 5432

CMD ["postgres"]
