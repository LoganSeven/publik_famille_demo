document.addEventListener('DOMContentLoaded', function() {
  fetch('/api/logged-errors-recent-count').then((response) => {
    if (! response.ok) {
      return
    }
    return response.json()
  }).then((json) => {
    if (json.msg) {
      const div = document.createElement('div')
      div.setAttribute('hidden', 'hidden')  // hidden unless supported by CSS
      div.className = 'logged-errors-recent-count'
      div.innerHTML = `<p><a href="/backoffice/studio/logged-errors/">${json.msg}</a></p>`
      document.body.appendChild(div)
    }
  })
})
