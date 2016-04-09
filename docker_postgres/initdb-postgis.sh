#!/bin/sh

set -e

# Perform all actions as user 'postgres'
export PGUSER=postgres
export PGDATA=$PGDATA

psql <<EOSQL
CREATE USER temba WITH PASSWORD '$TEMBAPASSWD';
ALTER ROLE temba CREATEROLE CREATEDB;
ALTER USER temba WITH SUPERUSER;
EOSQL

psql -U temba  postgres<<EOSQL
CREATE DATABASE temba;
UPDATE pg_database SET datistemplate = TRUE WHERE datname = 'temba';
EOSQL


cd /usr/share/postgresql/$PG_MAJOR/contrib/postgis-$POSTGIS_MAJOR
#psql --dbname temba < postgis.sql # commented to avoid probles with django
psql --dbname temba < topology.sql
psql --dbname temba -c 'create extension hstore;'
