window.matcher= (flag, subtext) ->
  regexp = new RegExp("(?:^|\\s)@([()A-Za-z_\.\+]*(?:[ ]*[+][ ]*[()A-Za-z_,\.\+]*|,[ ]*[()A-Za-z_,\.\+]*|$)*)$", "gi")
  match = regexp.exec(subtext)
  if match
    match[2] || match[1]
