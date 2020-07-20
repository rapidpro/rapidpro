# RapidPro [![Build Status](https://travis-ci.org/rapidpro/rapidpro.svg?branch=master)](https://travis-ci.org/rapidpro/rapidpro) [![codecov](https://codecov.io/gh/rapidpro/rapidpro/branch/master/graph/badge.svg)](https://codecov.io/gh/rapidpro/rapidpro)    

RapidPro is a hosted service for visually building interactive messaging applications.
To learn more, please visit the project site at http://rapidpro.github.io/rapidpro.

### Stable Versions

The set of versions that make up the latest stable release are:

 * [RapidPro v5.6.5](https://github.com/rapidpro/rapidpro/releases/tag/v5.6.5)
 * [Mailroom v5.6.1](https://github.com/nyaruka/mailroom/releases/tag/v5.6.1)
 * [Courier v5.6.0](https://github.com/nyaruka/courier/releases/tag/v5.6.0)
 * [Archiver v5.6.0](https://github.com/nyaruka/rp-archiver/releases/tag/v5.6.0)
 * [Indexer v5.6.0](https://github.com/nyaruka/rp-indexer/releases/tag/v5.6.0)
 * [Android Channel v2.0.0](https://github.com/rapidpro/android-channel/releases/tag/v2.0.0)
 * [Android Surveyor v13.5.0](https://github.com/rapidpro/surveyor/releases/tag/v13.5.0)

### Versioning in RapidPro

Major releases of RapidPro are made every four months on a set schedule. We target November 1st
as a major release (`v6.0.0`), then March 1st as the first stable dot release (`v6.2.0`) and July 1st
as the second stable dot release (`v6.4.0`). The next November would start the next major release `v7.0.0`.

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

RapidPro depends on other components such as Mailroom and Courier. These are versioned to follow the minor releases of RapidPro but may have patch releases made independently of patches to RapidPro. Other optional components such as the Android applications have their own versioning and release schedules. Each stable release of RapidPro details which version of these dependencies you need to run with it.

## Updating FlowEditor version

```
% npm install @nyaruka/flow-editor@whatver-version --save
```

### Get Involved

To run RapidPro for development, follow the Quick Start guide at http://rapidpro.github.io/rapidpro/docs/development.

### License

In late 2014, Nyaruka partnered with UNICEF to expand on the capabilities of TextIt and release the source code as RapidPro under the Affero GPL (AGPL) license.

In brief, the Affero license states you can use the RapidPro source for any project free of charge, but that any changes you make to the source code must be available to others. Note that unlike the GPL, the AGPL requires these changes to be made public even if you do not redistribute them. If you host a version of RapidPro, you must make the same source you are hosting available for others.

The software is provided under AGPL-3.0. Contributions to this project are accepted under the same license.
