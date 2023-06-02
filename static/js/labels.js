function getCheckedIds() {
  var checkedIds = Array();
  var checks = document.querySelectorAll('.object-row.checked');
  for (var i = 0; i < checks.length; i++) {
    checkedIds.push(parseInt(checks[i].getAttribute('data-object-id')));
  }
  return checkedIds.sort(numericComparator);
}

function getCheckedUuids() {
  var checkedUuids = Array();
  var checks = document.querySelectorAll('.object-row.checked');
  for (var i = 0; i < checks.length; i++) {
    checkedUuids.push(checks[i].getAttribute('data-uuid'));
  }
  return checkedUuids.sort();
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
    labelIds.push(parseInt($(labels[i]).data('id')));
  }
  return labelIds.sort(numericComparator);
}

function runActionOnObjectRows(action, onSuccess) {
  var objectIds = getCheckedIds();
  jQuery.ajaxSettings.traditional = true;
  fetchPJAXContent(document.location.href, '#pjax', {
    postData: { objects: objectIds, action: action, pjax: 'true' },
    onSuccess: onSuccess,
  });
}

function unlabelObjectRows(labelId, onSuccess) {
  var objectsIds = getCheckedIds();
  var addLabel = false;

  jQuery.ajaxSettings.traditional = true;
  fetchPJAXContent(document.location.href, '#pjax', {
    postData: {
      objects: objectsIds,
      label: labelId,
      add: addLabel,
      action: 'unlabel',
      pjax: 'true',
    },
    onSuccess: onSuccess,
  });
}

function postLabelChanges(
  smsIds,
  labelId,
  addLabel,
  number,
  onError,
  onSuccess
) {
  fetchPJAXContent(document.location.href, '#pjax', {
    postData: {
      objects: smsIds,
      label: labelId,
      add: addLabel,
      action: 'label',
      pjax: 'true',
      number: number,
    },
    onSuccess: function (data, textStatus) {
      recheckIds();
      if (onSuccess) {
        onSuccess();
      }
    },
    onError: onError,
  });
}

function labelObjectRows(labelId, forceRemove, onSuccess) {
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

  jQuery.ajaxSettings.traditional = true;
  window.lastChecked = getCheckedIds();

  if (objectRowsIds.length == 0) {
    showWarning(
      '{% trans "No rows selected" %}',
      '{% trans "Please select one or more rows before continuing." %}'
    );
    return;
  }

  postLabelChanges(objectRowsIds, labelId, addLabel, null, null, onSuccess);
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
    listButtons.classList.add('visible');
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
    listButtons.add('visible');
  } else {
    listButtons.remove('visible');
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
