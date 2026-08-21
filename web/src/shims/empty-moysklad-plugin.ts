/**
 * Web dashboard embeds @desktop/app for chat/settings. That eagerly globs
 * every apps/desktop/src/plugins plugin entry — including MoySklad, which
 * pulls ECharts/Plotly into the SPA. MoySklad UI on the web is the separate
 * IIFE under plugins/moysklad/dashboard/dist, so the desktop React plugin is
 * stubbed out of the web graph.
 */
const plugin = {
  id: 'moysklad',
  name: 'MoySklad',
  defaultEnabled: false,
  register() {
    // no-op — web uses dashboard plugin bundle
  }
}

export default plugin
