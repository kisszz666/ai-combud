<template>
  <div class="app-container">
    <header class="header">
      <h1>⚡ 智能电脑配置生成系统</h1>
      <p>根据您的预算和需求，AI智能推荐最优配置</p>
    </header>

    <main class="main-content">
      <section class="input-section">
        <h2 style="color: #b794f6; margin-bottom: 25px; font-size: 1.3em;">📋 配置需求</h2>

        <div class="form-group">
          <label for="budget">💰 预算金额（元）</label>
          <div class="input-wrapper">
            <span class="currency-symbol">¥</span>
            <input
              type="number"
              id="budget"
              v-model="budget"
              placeholder="请输入您的预算，例如：5000"
              min="1000"
              max="100000"
            />
          </div>
        </div>

        <div class="form-group">
          <label>🎮 使用场景（点击选择或手动输入）</label>
          <div class="use-case-tags">
            <div
              v-for="tag in useCaseTags"
              :key="tag"
              :class="['use-case-tag', { selected: selectedTag === tag }]"
              @click="selectTag(tag)"
            >
              {{ tag }}
            </div>
          </div>
          <textarea
            v-model="useCase"
            placeholder="或直接描述您的使用需求，例如：主要玩3A游戏，偶尔做视频剪辑..."
          ></textarea>
        </div>

        <button
          class="generate-btn"
          :class="{ loading: isLoading }"
          @click="generateConfig"
          :disabled="isLoading || !budget || !useCase"
        >
          <span v-if="isLoading">⏳ AI正在生成配置...</span>
          <span v-else>🚀 生成智能配置</span>
        </button>
      </section>

      <section v-if="isLoading" class="result-section">
        <div class="loading-section">
          <div class="loading-spinner"></div>
          <p class="loading-text">AI正在为您匹配最优配置，请稍候...</p>
          <p class="loading-text" style="font-size: 0.9em; margin-top: 10px;">（首次生成需要几秒时间，价格实时从网络获取）</p>
        </div>
      </section>

      <section v-else-if="result" class="result-section">
        <div class="result-header">
          <h2>🎯 推荐配置单</h2>
          <div class="budget-info">
            <div :class="['total', { exceeded: !result.success }]">¥{{ result.total_price.toFixed(0) }}</div>
            <div class="budget">预算: ¥{{ result.budget.toFixed(0) }}
              <span v-if="result.total_price <= result.budget" style="color: #4caf50;">✓</span>
              <span v-else style="color: #ff6b6b;">⚠ 超支</span>
            </div>
          </div>
        </div>

        <div v-if="result.success" class="success-message">
          ✅ 配置生成成功！总价控制在预算范围内
        </div>
        <div v-else class="error-message">
          ❌ {{ result.message }}
        </div>

        <div v-if="!result.success && result.total_price > result.budget * 1.3" class="budget-alert">
          ⚠️ 严重超预算！总价 ¥{{ result.total_price.toFixed(0) }} 远超预算 ¥{{ result.budget.toFixed(0) }}
          （超出 {{ ((result.total_price / result.budget - 1) * 100).toFixed(0) }}%）。
          请尝试降低预算重新生成，或检查是否为特定配件匹配到了过高价格。
        </div>

        <table class="config-table">
          <thead>
            <tr>
              <th>配件类型</th>
              <th>型号</th>
              <th>推荐理由</th>
              <th>参考价格</th>
              <th>购买链接</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="config in result.configs" :key="config.category">
              <td>
                <span class="category-icon">{{ getCategoryIcon(config.category) }}</span>
                {{ config.category }}
              </td>
              <td>
                <strong>{{ config.model }}</strong>
              </td>
              <td style="color: #9b9b9b; font-size: 0.9em;">{{ config.reason }}</td>
              <td>
                <span v-if="config.price" :class="['price', { error: config.price_status === 'failed' }]">
                  ¥{{ config.price.toFixed(0) }}
                </span>
                <span v-else-if="config.price_status === 'failed'" class="status-badge error">
                  获取失败
                </span>
                <span v-else class="status-badge" style="background: rgba(255,165,0,0.2); color: #ffa500;">
                  待查询
                </span>
              </td>
              <td>
                <a v-if="config.link" :href="config.link" target="_blank" class="price-link">
                  查看详情 →
                </a>
                <span v-else style="color: #666;">-</span>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="result.retry_count > 0" style="margin-top: 20px; color: #9b9b9b; font-size: 0.9em;">
          提示: AI已自动调整 {{ result.retry_count }} 次以匹配预算
        </div>
      </section>

      <section v-else class="result-section">
        <div class="empty-state">
          <div class="icon">💻</div>
          <h3>等待您的配置需求</h3>
          <p style="margin-top: 10px;">输入预算和使用场景，AI将为您智能推荐最优配置</p>
        </div>
      </section>
    </main>

    <footer style="text-align: center; padding: 40px 20px; color: #666; font-size: 0.9em;">
      <p>⚡ 智能电脑配置生成系统 · 价格实时从网络获取</p>
      <p style="margin-top: 10px;">数据来源：中关村在线 | AI驱动：DeepSeek</p>
    </footer>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const API_URL = ''

const budget = ref(5000)
const useCase = ref('')
const selectedTag = ref('')
const isLoading = ref(false)
const result = ref(null)

const useCaseTags = [
  '🎮 3A游戏大作',
  '🖥️ 电竞网游',
  '🎬 视频剪辑',
  '🏢 办公家用',
  '🎨 设计创作',
  '🤖 AI训练'
]

const categoryIcons = {
  'CPU': '🧠',
  '显卡': '🎮',
  '内存': '💾',
  '固态硬盘': '💿',
  '主板': '🔌',
  '电源': '⚡',
  '机箱': '📦'
}

function getCategoryIcon(category) {
  return categoryIcons[category] || '🔧'
}

function selectTag(tag) {
  selectedTag.value = tag
  useCase.value = tag.replace(/^[^a-zA-Z一-龥]+/, '').trim()
}

async function generateConfig() {
  if (!budget.value || !useCase.value.trim()) {
    alert('请输入预算金额和使用场景')
    return
  }

  isLoading.value = true
  result.value = null

  try {
    const response = await fetch(`${API_URL}/api/generate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        budget: Number(budget.value),
        use_case: useCase.value.trim()
      })
    })

    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || '请求失败')
    }

    const data = await response.json()
    result.value = data
  } catch (error) {
    console.error('Error:', error)
    alert(`生成失败: ${error.message}\n\n请确保后端服务已启动 (运行 python backend/main.py)`)
  } finally {
    isLoading.value = false
  }
}
</script>
