// prettier-ignore
window.urls = [
  { old: /\/channels\/(.*)\/logs\/msg\/(.*)/,            new: /\/settings\/channels\/(.*)\/logs\/msg\/(.*)/ },
  { old: /\/msg\/filter\/(.*)/,                          new: /\/messages\/labels\/(.*)/ },
  { old: /\/msg\/flow\//,                                new: /\/messages\/flows/ },
  { old: /\/msg\/(.*)\//,                                new: /\/messages\/(.*)/ },
  { old: /\/contact\/read\/(.*)/,                        new: /\/contacts\/read\/(.*)/ },
  { old: /\/contact\/filter\/(.*)/,                      new: /\/contacts\/groups\/(.*)/ },
  { old: /\/contact\//,                                  new: /\/contacts\// },
  { old: /\/flow\/editor\/(.*)/,                         new: /\/flows\/editor\/(.*)/ },
  { old: /\/flow\/results\/(.*)/,                        new: /\/flows\/results\/(.*)/ },
  { old: /\/flow\/filter\/(.*)\//,                       new: /\/flows\/labels\/(.*)/ },
  { old: /\/flow\//,                                     new: /\/flows\/active/ },
  { old: /\/flow\/(.*)\//,                               new: /\/flows\/(.*)\// },
  { old: /\/ticket\/(.*)\/(.*)\/(.*)/,                   new: /\/tickets\/(.*)\/(.*)\/(.*)/ },
  { old: /\/trigger\//,                                  new: /\/triggers\/active\// },
  { old: /\/trigger\/(.*)\//,                            new: /\/trigger\/(.*)/ },
  { old: /\/campaignevent\/read\/(.*)\/(.*)/,            new: /\/campaigns\/(.*)\/(.*)/ },
  { old: /\/campaign\/read\/(.*)\//,                     new: /\/campaigns\/(.*)/ },
  { old: /\/campaign\//,                                 new: /\/campaigns\/active/ },
  { old: /\/channels\/logs\/(.*)\//,                     new: /\/settings\/channels\/(.*)\/history\// },
  { old: /\/channels\/channel\/configuration\/(.*)\//,   new: /\/settings\/channels\/(.*)\/config\// },
  { old: /\/channels\/channel\/read\/(.*)/,              new: /\/settings\/channels\/(.*)/ },
  { old: /\/channels\/channel\/claim\//,                 new: /\/settings\/workspace\/new-channel\// },
  { old: /\/channels\/types\/(.*)\/claim/,               new: /\/settings\/workspace\/new-(.*)\// },
  { old: /\/classifier\/connect.*/,                      new: /\/settings\/workspace\/new-classifier\// },
  { old: /\/classifiers\/types\/(.*)/,                   new: /\/settings\/classifiers\/types\/(.*)/ },
  { old: /\/httplog\/classifier\/(.*)\//,                new: /\/settings\/classifiers\/(.*)\/history\// },
  { old: /\/httplog\/read\/(.*)\//,                      new: /\/settings\/httplog\/(.*)\// },
  { old: /\/classifier\/read\/(.*)\//,                   new: /\/settings\/classifiers\/(.*)\// },
  { old: /\/org\/manage_accounts\/(.*)/,                 new: /\/settings\/users/ },
  { old: /\/user\/account\//,                            new: /\/settings\/account/ },
  { old: /\/user\/two_factor_disable\//,                 new: /\/settings\/authentication\/2fa-disable/ },
  { old: /\/org\/export\//,                              new: /\/settings\/workspace\/export\// },
  { old: /\/org\/import\//,                              new: /\/settings\/workspace\/import\// },
  { old: /\/org\/read\/(.*)/,                            new: /\/staff\/workspace\/(.*)/ },
  { old: /\/user\/update\/(.*)/,                         new: /\/staff\/user\/(.*)/ },
  { old: /\/org\/update\/(.*)/,                          new: /\/staff\/workspaces\/(.*)\/update/ },
  { old: /\/org\/home\//,                                new: /\/settings\/workspace\// },
  { old: /\/org\/manage_accounts_sub_org\/\?org=(.*)/,   new: /\/settings\/(.*)\// },

];

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
