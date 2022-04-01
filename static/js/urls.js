// prettier-ignore
window.urls = [
  { old: /\/msg\/filter\/(.*)/,                          new: /\/messages\/labels\/(.*)\// },
  { old: /\/contact\/read\/(.*)/,                        new: /\/contacts\/read\/(.*)/ },
  { old: /\/contact\/filter\/(.*)/,                      new: /\/contacts\/groups\/(.*)\// },
  { old: /\/contact\//,                                  new: /\/contacts\// },
  { old: /\/flow\/editor\/(.*)\//,                       new: /\/flows\/editor\/(.*)/ },
  { old: /\/flow\/filter\/(.*)\//,                       new: /\/flows\/(.*)\// },
  { old: /\/ticket\/(.*)/,                               new: /\/tickets\/(.*)\// },
  { old: /\/campaignevent\/read\/(.*)\/(.*)/,            new: /\/campaigns\/(.*)\/(.*)/ },
  { old: /\/campaign\/read\/(.*)\//,                     new: /\/campaigns\/(.*)\// },
  { old: /\/channels\/channellog\/read\/(.*)\/(.*)\//,   new: /\/settings\/ch-(.*)\/log\/(.*)/ },
  { old: /\/channels\/channellog\/(.*)\//,               new: /\/settings\/ch-(.*)\/log/ },
  { old: /\/channels\/channel\/configuration\/(.*)\//,   new: /\/settings\/ch-(.*)\/config/ },
  { old: /\/channels\/channel\/read\/(.*)/,              new: /\/settings\/ch-(.*)\// },
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
