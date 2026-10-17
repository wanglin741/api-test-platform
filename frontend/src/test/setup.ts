import { enableAutoUnmount } from '@vue/test-utils'
import { afterEach } from 'vitest'

// 每个用例后卸载挂载的组件,顺带清掉 ElMessage 等挂在 body 上的残留
enableAutoUnmount(afterEach)
