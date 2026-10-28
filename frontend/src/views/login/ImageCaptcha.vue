<template>
  <div class="image-captcha">
    <el-input
      v-model="input"
      maxlength="4"
      clearable
      placeholder="图形验证码"
      aria-label="图形验证码"
    />
    <button
      type="button"
      class="image-captcha__canvas"
      aria-label="刷新图形验证码"
      @click="refresh"
    >
      <canvas
        ref="canvasRef"
        width="112"
        height="40"
        aria-hidden="true"
      />
      <span v-if="!canvasReady" class="image-captcha__fallback">
        {{ code }}
      </span>
    </button>
    <el-button
      text
      circle
      aria-label="刷新图形验证码"
      @click="refresh"
    >
      <el-icon><Refresh /></el-icon>
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'

const input = defineModel<string>('modelValue', { default: '' })
const code = ref('')
const canvasReady = ref(true)
const canvasRef = ref<HTMLCanvasElement | null>(null)

const CHARACTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

function generateCode(): string {
  let result = ''
  for (let index = 0; index < 4; index += 1) {
    result += CHARACTERS[Math.floor(Math.random() * CHARACTERS.length)]
  }
  return result
}

function drawCaptcha(): void {
  const canvas = canvasRef.value
  const context = canvas?.getContext('2d')
  if (!canvas || !context) {
    canvasReady.value = false
    return
  }

  canvasReady.value = true
  const { width, height } = canvas
  context.clearRect(0, 0, width, height)
  context.fillStyle = '#eef2f7'
  context.fillRect(0, 0, width, height)

  for (let index = 0; index < 5; index += 1) {
    context.strokeStyle = `hsl(${Math.random() * 360} 45% 65% / 0.55)`
    context.beginPath()
    context.moveTo(Math.random() * width, Math.random() * height)
    context.lineTo(Math.random() * width, Math.random() * height)
    context.stroke()
  }

  code.value.split('').forEach((character, index) => {
    context.save()
    context.translate(18 + index * 24, 27)
    context.rotate((Math.random() - 0.5) * 0.35)
    context.fillStyle = `hsl(${205 + Math.random() * 80} 45% 30%)`
    context.font = '600 21px "Microsoft YaHei", sans-serif'
    context.fillText(character, 0, 0)
    context.restore()
  })
}

function refresh(): void {
  code.value = generateCode()
  input.value = ''
  void nextTick(drawCaptcha)
}

function validate(): boolean {
  return input.value.trim().toUpperCase() === code.value
}

onMounted(refresh)

defineExpose({
  refresh,
  validate,
})
</script>

<style scoped>
.image-captcha {
  width: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 112px 32px;
  align-items: center;
  gap: 8px;
}

.image-captcha__canvas {
  position: relative;
  width: 112px;
  height: 40px;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: #eef2f7;
  cursor: pointer;
}

.image-captcha__canvas canvas {
  display: block;
  width: 112px;
  height: 40px;
}

.image-captcha__fallback {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: #334155;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 3px;
}
</style>
