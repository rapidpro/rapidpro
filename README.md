# RapidPro 

[![Build Status](https://github.com/nyaruka/rapidpro/workflows/CI/badge.svg)](https://github.com/nyaruka/rapidpro/actions?query=workflow%3ACI) 
[![codecov](https://codecov.io/gh/nyaruka/rapidpro/branch/main/graph/badge.svg)](https://codecov.io/gh/nyaruka/rapidpro)

RapidPro is a hosted service for visually building interactive messaging applications.
To learn more, please visit the project site at http://rapidpro.github.io/rapidpro.

### Stable Versions

The set of versions that make up the latest stable release are:

 * [RapidPro v7.4.2](https://github.com/rapidpro/rapidpro/releases/tag/v7.4.2)
 * [Mailroom v7.4.1](https://github.com/rapidpro/mailroom/releases/tag/v7.4.1)
 * [Courier v7.4.0](https://github.com/nyaruka/courier/releases/tag/v7.4.0)
 * [Archiver v7.4.0](https://github.com/nyaruka/rp-archiver/releases/tag/v7.4.0)
 * [Indexer v7.4.0](https://github.com/nyaruka/rp-indexer/releases/tag/v7.4.0)
 * [Android Channel v2.0.0](https://github.com/rapidpro/android-channel/releases/tag/v2.0.0)
 * [Android Surveyor v13.9.0](https://github.com/rapidpro/surveyor/releases/tag/v13.9.0)

### Versioning in RapidPro

Major releases of RapidPro are made every four months on a set schedule. We target November 1st
as a major release (`v7.0.0`), then March 1st as the first stable dot release (`v7.2.0`) and July 1st
as the second stable dot release (`v7.4.0`). The next November would start the next major release `v8.0.0`.

Unstable releases have odd minor versions, that is versions `v7.1.*` would indicate an unstable or *development*
version of RapidPro. Generally we recommend staying on stable releases unless you
have experience developing against RapidPro.

To upgrade from one stable release to the next, you should first install and run the migrations
for the latest stable release you are on, then every stable release afterwards. If you are
on version `v6.0.12` and the latest stable release on the `v6.0` series is `v6.0.14`, you should
first install `v6.0.14` before trying to install the next stable release `v6.2.5`.

Generally we only do bug fixes (patch releases) on stable releases for the first two weeks after we put
out that release. After that you either have to wait for the next stable release or take your
chances with an unstable release.
