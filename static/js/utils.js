function asBool(value) {
  if (typeof value == 'boolean') {
    return value;
  } else if (typeof value == 'string') {
    if (value.toLowerCase() == 'true') {
      return true;
    } else if (value.toLowerCase() == 'false') {
      return false;
    }
  }
  return null;
}
