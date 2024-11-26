function getCheckedIds() {
  var checkedIds = Array();
  var checks = document.querySelectorAll('.object-row.checked');
  for (var i = 0; i < checks.length; i++) {
    checkedIds.push(parseInt(checks[i].getAttribute('data-object-id')));
  }
  return checkedIds.sort(numericComparator);
}

function getLabeledIds(labelId) {
  var objectRowsIds = Array();
  var labeled = document.querySelectorAll(
    ".lbl[data-id='" + labelId + "'], temba-label[data-id='" + labelId + "']"
  );
  for (var i = 0; i < labeled.length; i++) {
    var row = labeled[i].closest('.object-row');
    var id = parseInt(row.getAttribute('data-object-id'));
    objectRowsIds.push(id);
  }
  return objectRowsIds.sort(numericComparator);
}

function getObjectRowLabels(objectId) {
  var labelIds = Array();
  var row = document.querySelector(
    ".object-row[data-object-id='" + objectId + "']"
  );
  var labels = row.querySelectorAll('.lbl, temba-label');
  for (var i = 0; i < labels.length; i++) {
    labelIds.push(parseInt(labels[i].dataset.id));
  }
  return labelIds.sort(numericComparator);
}

function runActionOnObjectRows(action, options = {}) {
  var objectIds = getCheckedIds();
  const formData = new FormData();
  if (options.label) {
    formData.append('label', options.label);
  }

  if (!options.add) {
    formData.append('add', 'false');
  }

  for (var i = 0; i < objectIds.length; i++) {
    formData.append('objects', objectIds[i]);
  }

  formData.append('action', action);
  return spaPost(document.location.href, {
    postData: formData
  });
}

function unlabelObjectRows(labelId) {
  runActionOnObjectRows('unlabel', { label: labelId, add: false });
}

function labelObjectRows(labelId, forceRemove) {
  var objectRowsIds = getCheckedIds();
  var labeledIds = getLabeledIds(labelId);

  // they all have the label, so we are actually removing this label
  var addLabel = false;
  for (var i = 0; i < objectRowsIds.length; i++) {
    var found = false;
    for (var j = 0; j < labeledIds.length; j++) {
      if (objectRowsIds[i] == labeledIds[j]) {
        found = true;
        break;
      }
    }
    if (!found) {
      addLabel = true;
      break;
    }
  }

  var checkbox = document.querySelector(
    '.lbl-menu[data-id="' + labelId + '"] temba-checkbox'
  );
  if (checkbox.checked) {
    addLabel = true;
  }

  if (forceRemove) {
    addLabel = false;
  }

  window.lastChecked = getCheckedIds();

  if (objectRowsIds.length == 0) {
    showWarning(
      '{% trans "No rows selected" %}',
      '{% trans "Please select one or more rows before continuing." %}'
    );
    return;
  }

  runActionOnObjectRows('label', { label: labelId, add: addLabel }).then(
    recheckIds
  );
}

/**
 * After post, we need to recheck ids that were previously checked
 */
function recheckIds() {
  if (window.lastChecked && window.lastChecked.length > 0) {
    for (var i = 0; i < window.lastChecked.length; i++) {
      var row = document.querySelector(
        ".object-row[data-object-id='" + window.lastChecked[i] + "']"
      );
      var checkbox = row.querySelector('temba-checkbox');
      checkbox.checked = true;
      row.classList.add('checked');
    }
    var listButtons = document.querySelector('.list-buttons-container');
    listButtons.classList.remove('hide');
    updateLabelMenu();
  }
}

function clearLabelMenu() {
  // remove all checked and partials
  var checkboxes = document.querySelectorAll('.lbl-menu temba-checkbox');
  checkboxes.forEach(function (checkbox) {
    checkbox.checked = false;
    checkbox.partial = false;
  });
}

// updates our label menu according to the currently selected set
function updateLabelMenu() {
  clearLabelMenu();

  var objectRowsIds = getCheckedIds();
  var updatedLabels = Object();

  for (var i = 0; i < objectRowsIds.length; i++) {
    var labelIds = getObjectRowLabels(objectRowsIds[i]);

    for (var j = 0; j < labelIds.length; j++) {
      var labelId = labelIds[j];

      if (!updatedLabels[labelId]) {
        var labeledIds = getLabeledIds(labelId);
        var objectRowIdsWithLabel = intersect(objectRowsIds, labeledIds);

        var checkbox = document.querySelector(
          '.lbl-menu[data-id="' + labelId + '"] temba-checkbox'
        );

        if (checkbox) {
          if (objectRowIdsWithLabel.length == objectRowsIds.length) {
            checkbox.partial = false;
            checkbox.checked = true;
          } else {
            checkbox.checked = false;
            checkbox.partial = true;
          }
          updatedLabels[labelId] = true;
        }
      }
    }
  }
}

function handleRowSelection(checkbox) {
  var row = checkbox.parentElement.parentElement.classList;
  var listButtons = document.querySelector('.list-buttons-container').classList;

  if (checkbox.checked) {
    row.add('checked');
    row.remove('unchecked');
  } else {
    row.remove('checked');
    row.add('unchecked');
  }

  if (document.querySelector('tr.checked')) {
    listButtons.remove('hide');
  } else {
    listButtons.add('hide');
  }
  updateLabelMenu();

  const selectAll = document.querySelector('temba-checkbox.select-all');
  const hasUnchecked = !!document.querySelector('tr.unchecked');

  if (selectAll.checked && hasUnchecked) {
    selectAll.partial = true;
    selectAll.checked = false;
  }

  if (selectAll.partial && !hasUnchecked) {
    selectAll.partial = false;
    selectAll.checked = true;
  }
}

function handleSelectAll(ele) {
  const checkboxes = document.querySelectorAll('.selectable.list td.checkbox');
  checkboxes.forEach(function (checkbox) {
    if (ele.checked || ele.partial) {
      if (!checkbox.parentElement.classList.contains('checked')) {
        checkbox.click();
      }
    } else {
      if (checkbox.parentElement.classList.contains('checked')) {
        checkbox.click();
      }
    }
  });
}

// Used for start flow and send message actions
function getCheckedUuids() {
  var checkedUuids = Array();
  var checks = document.querySelectorAll('.object-row.checked');
  for (var i = 0; i < checks.length; i++) {
    checkedUuids.push(checks[i].getAttribute('data-uuid'));
  }
  return checkedUuids.sort();
}
