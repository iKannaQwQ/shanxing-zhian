import { onMounted, onUnmounted, ref } from 'vue'

/** 视口状态：用于在窄屏切换布局，并把宽度显示在页脚便于排查。 */
export function useViewport(breakpoint = 768) {
  const width = ref(typeof window === 'undefined' ? 1280 : window.innerWidth)
  const isNarrow = ref(width.value <= breakpoint)

  function update() {
    width.value = window.innerWidth
    isNarrow.value = window.innerWidth <= breakpoint
  }

  onMounted(() => {
    update()
    window.addEventListener('resize', update)
  })
  onUnmounted(() => window.removeEventListener('resize', update))

  return { width, isNarrow }
}
