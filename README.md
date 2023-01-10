# TextIt

[![Build Status](https://github.com/nyaruka/rapidpro/workflows/CI/badge.svg)](https://github.com/nyaruka/rapidpro/actions?query=workflow%3ACI) 
[![codecov](https://codecov.io/gh/nyaruka/rapidpro/branch/main/graph/badge.svg)](https://codecov.io/gh/nyaruka/rapidpro)

TextIt is a hosted service for visually building interactive messaging applications. You can signup at 
[textit.com](https://textit.com) or host it yourself.

### Stable Versions

The set of versions that make up the latest stable release are:

 * [RapidPro v7.4.2](https://github.com/rapidpro/rapidpro/releases/tag/v7.4.2)
 * [Mailroom v7.4.1](https://github.com/rapidpro/mailroom/releases/tag/v7.4.1)
 * [Courier v7.4.0](https://github.com/nyaruka/courier/releases/tag/v7.4.0)
 * [Archiver v7.4.0](https://github.com/nyaruka/rp-archiver/releases/tag/v7.4.0)
 * [Indexer v7.4.0](https://github.com/nyaruka/rp-indexer/releases/tag/v7.4.0)
 * [Android Channel v2.0.0](https://github.com/rapidpro/android-channel/releases/tag/v2.0.0)
 * [Android Surveyor v13.9.0](https://github.com/rapidpro/surveyor/releases/tag/v13.9.0)

### Versioning

Major releases are made every 4 months on a set schedule. We target January as a major release (e.g. `8.0.0`), then 
May as the first stable dot release (e.g. `8.2.0`), and September as the second dot release (e.g. `8.4.0`). Unstable 
releases (i.e. *development* versionsw) have odd minor versions (e.g. `8.1.*`, `8.3.*`). Generally we recommend staying 
on stable releases.

To upgrade from one stable release to the next, you must first install and run the migrations
for the latest stable release you are on, then every stable release afterwards. For example if you're upgrading from 
`7.4` to `8.0`, you need to upgrade to `7.4.2` before upgrading to `8.0`

Generally we only do bug fixes (patch releases) on stable releases for the first two weeks after we put
out that release. After that you either have to wait for the next stable release or take your chances with an unstable 
release.
