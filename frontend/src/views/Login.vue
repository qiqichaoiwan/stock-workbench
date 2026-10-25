<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <div class="brand">
        <img src="/favicon.svg" width="40" height="40" alt="logo" />
        <div>
          <h2 class="brand-title">智能选股工作台</h2>
          <p class="muted">A股四层漏斗选股 · 持仓管理 · 仅供研究参考,不构成投资建议</p>
        </div>
      </div>
      <el-tabs v-model="tab">
        <el-tab-pane label="登录" name="login">
          <el-form ref="loginRef" :model="loginForm" :rules="loginRules" @submit.prevent>
            <el-form-item prop="username">
              <el-input v-model="loginForm.username" placeholder="用户名" />
            </el-form-item>
            <el-form-item prop="password">
              <el-input v-model="loginForm.password" type="password" placeholder="密码" show-password
                        @keyup.enter="doLogin" />
            </el-form-item>
            <el-button type="primary" style="width:100%" :loading="loading" @click="doLogin">登 录</el-button>
          </el-form>
        </el-tab-pane>
        <el-tab-pane label="注册" name="register">
          <el-alert type="info" :closable="false" style="margin-bottom:12px"
                    title="注册需邀请码,请联系管理员获取;管理员也可在后台直接创建账号。" />
          <el-form ref="regRef" :model="regForm" :rules="regRules" @submit.prevent>
            <el-form-item prop="username">
              <el-input v-model="regForm.username" placeholder="用户名(3-32 位)" />
            </el-form-item>
            <el-form-item prop="password">
              <el-input v-model="regForm.password" type="password" placeholder="密码(至少 8 位)" show-password />
            </el-form-item>
            <el-form-item prop="confirm">
              <el-input v-model="regForm.confirm" type="password" placeholder="确认密码" show-password />
            </el-form-item>
            <el-form-item prop="invite_code">
              <el-input v-model="regForm.invite_code" placeholder="邀请码(如 INV-XXXXXXXX)" />
            </el-form-item>
            <el-form-item prop="nickname">
              <el-input v-model="regForm.nickname" placeholder="昵称(可选)" />
            </el-form-item>
            <el-button type="success" style="width:100%" :loading="loading" @click="doRegister">凭邀请码注册</el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useUserStore } from '../store'

const router = useRouter()
const store = useUserStore()
const tab = ref('login')
const loading = ref(false)
const loginRef = ref()
const regRef = ref()
const loginForm = ref({ username: '', password: '' })
const regForm = ref({ username: '', password: '', confirm: '', invite_code: '', nickname: '' })

const loginRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}
const regRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 32, message: '用户名 3-32 位', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
  confirm: [
    {
      validator: (_r, v, cb) => (v === regForm.value.password ? cb() : cb(new Error('两次密码不一致'))),
      trigger: 'blur',
    },
  ],
  invite_code: [{ required: true, message: '请输入邀请码', trigger: 'blur' }],
}

async function doLogin() {
  await loginRef.value.validate()
  loading.value = true
  try {
    await store.login(loginForm.value.username, loginForm.value.password)
    ElMessage.success('登录成功')
    router.push('/dashboard')
  } finally {
    loading.value = false
  }
}

async function doRegister() {
  await regRef.value.validate()
  loading.value = true
  try {
    await api.post('/api/auth/register', {
      username: regForm.value.username,
      password: regForm.value.password,
      nickname: regForm.value.nickname,
      invite_code: regForm.value.invite_code,
    })
    ElMessage.success('注册成功,请登录')
    tab.value = 'login'
    loginForm.value.username = regForm.value.username
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  display: flex; align-items: center; justify-content: center; height: 100vh;
  background: linear-gradient(135deg, #1d2530 0%, #2b3a4e 55%, #3a5169 100%);
}
.login-card { width: min(430px, 94vw); border-radius: 10px; }
.brand { display: flex; gap: 12px; align-items: center; margin-bottom: 6px; }
.brand-title { margin: 0; font-size: 20px; }
.muted { color: #909399; font-size: 12px; margin: 2px 0 4px; }
</style>
