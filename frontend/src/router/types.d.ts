import 'vue-router'

export {}

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    permission?: string
    public?: boolean
    parentTitle?: string
    parentPath?: string
  }
}
