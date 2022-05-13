// prettier-ignore
window.urls = [
  { old: /\/msg\/filter\/(.*)/,                          new: /\/messages\/labels\/(.*)\// },
  { old: /\/msg\/flow\//,                                new: /\/messages\/flows\// },
  { old: /\/msg\/(.*)\//,                                new: /\/messages\/(.*)\// },
  { old: /\/contact\/read\/(.*)/,                        new: /\/contacts\/read\/(.*)/ },
  { old: /\/contact\/filter\/(.*)/,                      new: /\/contacts\/groups\/(.*)\// },
  { old: /\/contact\//,                                  new: /\/contacts\// },
  { old: /\/flow\/editor\/(.*)\//,                       new: /\/flows\/editor\/(.*)/ },
  { old: /\/flow\/filter\/(.*)\//,                       new: /\/flows\/labels\/(.*)\// },
  { old: /\/flow\//,                                     new: /\/flows\/active\// },
  { old: /\/flow\/(.*)\//,                               new: /\/flows\/(.*)\// },
  { old: /\/trigger\//,                                  new: /\/triggers\/active\// },
  { old: /\/trigger\/(.*)\//,                            new: /\/trigger\/(.*)\// },
  { old: /\/campaignevent\/read\/(.*)\/(.*)/,            new: /\/campaigns\/(.*)\/(.*)/ },
  { old: /\/campaign\/read\/(.*)\//,                     new: /\/campaigns\/(.*)\// },
  { old: /\/campaign\//,                                 new: /\/campaigns\/active\// },
  { old: /\/channels\/channellog\/read\/(.*)\/(.*)\//,   new: /\/settings\/channels\/(.*)\/log\/(.*)/ },
  { old: /\/channels\/channellog\/(.*)\//,               new: /\/settings\/channels\/(.*)\/history\// },
  { old: /\/channels\/channel\/configuration\/(.*)\//,   new: /\/settings\/channels\/(.*)\/config\// },
  { old: /\/channels\/channel\/read\/(.*)/,              new: /\/settings\/channels\/(.*)\// },
  { old: /\/channels\/types\/(.*)\/claim/,               new: /\/settings\/channel\/(.*)\// },
  { old: /\/org\/manage_accounts\/(.*)/,                 new: /\/settings\/logins/ },
  { old: /\/user\/two_factor_disable\//,                 new: /\/settings\/authentication\/2fa-disable\// }
]

window.mapUrl = function (path, reverse) {
    var findDirection = reverse ? 'new' : 'old';
    var replaceDirection = reverse ? 'old' : 'new';
    for (var mapping of urls) {
        var match = path.match(mapping[findDirection]);
        if (match) {
            path = mapping[replaceDirection].source.replaceAll('\\/', '/');
            for (var i = 1; i < match.length; i++) {
                path = path.replace('(.*)', match[i]);
            }
            path = path.replaceAll('(.*)', '');
            return path;
        }
    }
    return path;
};
