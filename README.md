# Pulse Engage (IST Research RapidPro Fork)

This repository is a fork of the RapidPro library used for developing additional functionality.
# RapidPro [![Build Status](https://travis-ci.org/rapidpro/rapidpro.svg?branch=master)](https://travis-ci.org/rapidpro/rapidpro) [![codecov](https://codecov.io/gh/rapidpro/rapidpro/branch/master/graph/badge.svg)](https://codecov.io/gh/rapidpro/rapidpro)    

### Version v5.0.0
See [Pulse Engage Setup and Deployment](https://istresearch.atlassian.net/wiki/spaces/PULSE/pages/506069039/Pulse+Engage+RapidPro+Setup+and+Deployment) for instructions on building and deploying docker containers to remote servers.

### Stable Versions

The set of versions that make up the latest stable release are:

 * [RapidPro v5.0.0](https://github.com/rapidpro/rapidpro/releases/tag/v5.0.0)
 * [Mailroom v2.0.0](https://github.com/nyaruka/mailroom/releases/tag/v2.0.0)
 * [Courier v2.0.0](https://github.com/nyaruka/courier/releases/tag/v2.0.0)
 * [Archiver v2.0.0](https://github.com/nyaruka/rp-archiver/releases/tag/v2.0.0)
 * [Indexer v2.0.0](https://github.com/nyaruka/rp-indexer/releases/tag/v2.0.0)
 * [Android Channel v2.0.0](https://github.com/rapidpro/android-channel/releases/tag/v2.0.0)
 * [Android Surveyor v13.1.0](https://github.com/rapidpro/surveyor/releases/tag/v13.1.0)

### Versioning in RapidPro

Major releases of RapidPro are made every four months on a set schedule. We target March 1st
as a major release (`v5.0.0`), then July 1st as the first stable dot release (`v5.2.0`) and November 1st
as the second stable dot release (`v5.4.0`). The next March would start the next major release `v6.0.0`.

Unstable releases have odd minor versions, that is versions `v5.1.*` would indicate an unstable or *development*
version of RapidPro. Generally we recommend staying on stable releases unless you
have experience developing against RapidPro.

To upgrade from one stable release to the next, you should first install and run the migrations
for the latest stable release you are on, then every stable release afterwards. If you are
on version `v5.0.12` and the latest stable release on the `v5.0` series is `v5.0.14`, you should
first install `v5.0.14` before trying to install the next stable release `v5.2.5`.

Generally we only do bug fixes (patch releases) on stable releases for the first two weeks after we put
out that release. After that you either have to wait for the next stable release or take your
chances with an unstable release.

### Versioning of other Components

RapidPro depends on other components such as Mailroom and Courier. These are versioned
differently than RapidPro and do not follow a set schedule. Each stable release of RapidPro
details which version of these dependencies you need to run with it.

### Get Involved

```
docker pull rapidpro/rapidpro:master
```

If you are running RapidPro for the first time, please ensure the following variable is set for the rapidpro service:
```
- MANAGEPY_MIGRATE=on
```

It will allow the database to be initialized.

To stand up a development instance, simply run:

```
./dc-rapidpro.dev.sh up --build -d
```

RapidPro should now be available at `0.0.0.0:8000`.


Any local changes will be picked up by the development instance. If, in any case, there are changes that do not appear in the development instance, ensure that the files are properly mounted in `rapidpro-compose.dev.yaml`
The software is provided under AGPL-3.0. Contributions to this project are accepted under the same license.
