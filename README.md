# RapidPro 

[![Build Status](https://github.com/rapidpro/rapidpro/workflows/CI/badge.svg)](https://github.com/rapidpro/rapidpro/actions?query=workflow%3ACI) 
[![codecov](https://codecov.io/gh/rapidpro/rapidpro/branch/main/graph/badge.svg)](https://codecov.io/gh/rapidpro/rapidpro)

RapidPro is a hosted service for visually building interactive messaging applications.
To learn more, please visit the project site at http://rapidpro.github.io/rapidpro.

### Stable Versions

The set of versions that make up the latest stable release are:

 * [RapidPro v7.4.1](https://github.com/rapidpro/rapidpro/releases/tag/v7.2.4)
 * [Mailroom v7.4.1](https://github.com/nyaruka/mailroom/releases/tag/v7.2.6)
 * [Courier v7.4.0](https://github.com/nyaruka/courier/releases/tag/v7.2.0)
 * [Archiver v7.4.0](https://github.com/nyaruka/rp-archiver/releases/tag/v7.2.0)
 * [Indexer v7.4.0](https://github.com/nyaruka/rp-indexer/releases/tag/v7.2.0)
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

### Versioning of other Components

RapidPro depends on other components such as Mailroom and Courier. These are versioned to follow the minor releases of 
RapidPro but may have patch releases made independently of patches to RapidPro. Other optional components such as the 
Android applications have their own versioning and release schedules. Each stable release of RapidPro details which 
version of these dependencies you need to run with it.

## Updating FlowEditor version

```
% npm install @nyaruka/flow-editor@whatever-version --save
```

### Get Involved

To run RapidPro for development, follow the Quick Start guide at http://rapidpro.github.io/rapidpro/docs/development.

### License

In late 2014, Nyaruka partnered with UNICEF to expand on the capabilities of TextIt and release the source code as 
RapidPro under the Affero GPL (AGPL) license.

In brief, the Affero license states you can use the RapidPro source for any project free of charge, but that any changes 
you make to the source code must be available to others. Note that unlike the GPL, the AGPL requires these changes to be 
made public even if you do not redistribute them. If you host a version of RapidPro, you must make the same source you 
are hosting available for others.

The software is provided under AGPL-3.0. Contributions to this project are accepted under the same license.
