# RapidPro 

[![Build Status](https://github.com/rapidpro/rapidpro/workflows/CI/badge.svg)](https://github.com/rapidpro/rapidpro/actions?query=workflow%3ACI) 
[![codecov](https://codecov.io/gh/rapidpro/rapidpro/branch/main/graph/badge.svg)](https://codecov.io/gh/rapidpro/rapidpro)

RapidPro is a platform for visually building interactive messaging applications.
To learn more, please visit the project site at http://rapidpro.github.io/rapidpro.

### Stable Versions

The set of versions that make up the latest stable release are:

 * [RapidPro 8.0.1](https://github.com/rapidpro/rapidpro/releases/tag/v8.0.1)
 * [Mailroom 8.0.0](https://github.com/rapidpro/mailroom/releases/tag/v8.0.0)
 * [Courier 8.0.2](https://github.com/nyaruka/courier/releases/tag/v8.0.2)
 * [Archiver 8.0.0](https://github.com/nyaruka/rp-archiver/releases/tag/v8.0.0)
 * [Indexer 8.0.0](https://github.com/nyaruka/rp-indexer/releases/tag/v8.0.0)

### Versioning

Major releases are made every 6 months on a set schedule. We target January as a major release (e.g. `9.0.0`), then 
July as the stable dot release (e.g. `9.2.0`). Unstable releases (i.e. *development* versions) have odd minor versions 
(e.g. `9.1.*`, `9.3.*`). Generally we recommend staying on stable releases.

To upgrade from one stable release to the next, you must first install and run the migrations
for the latest stable release you are on, then every stable release afterwards. For example if you're upgrading from 
`7.4` to `8.0`, you need to upgrade to `7.4.2` before upgrading to `8.0`

Generally we only do bug fixes (patch releases) on stable releases for the first two weeks after we put
out that release. After that you either have to wait for the next stable release or take your chances with an unstable 
release.
