// Expects one or more elements that have a an attribute of
// "data-hash" which gets used for the generation of the hashicon
var hashicons = document.querySelectorAll('.hashicon-wrapper')
hashicons.forEach(appendLargeHashIcon)

var hashiconsSmall = document.querySelectorAll('.hashicon-wrapper-small')
hashiconsSmall.forEach(appendSmallHashIcon)

function appendLargeHashIcon (iconEl) {
  appendHashIcon(iconEl, 30)
}

function appendSmallHashIcon (iconEl) {
  appendHashIcon(iconEl, 15)
}

function appendHashIcon (iconEl, hashIconSize) {
  if(!iconEl.dataset.hasOwnProperty('hash')) {
    return
  }
  var datasetHash = iconEl.dataset.hash
  var hashIcon = hashicon(datasetHash, hashIconSize)
  iconEl.appendChild(hashIcon)
}
