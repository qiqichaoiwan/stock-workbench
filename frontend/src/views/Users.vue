<template>
  <div>
    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>用户管理</span>
          <span class="muted" style="font-size:12px">admin=全部权限 · analyst=选股工作台操作 · user=只读看板+自己持仓</span>
          <span class="grow" />
          <el-input v-model="keyword" placeholder="搜索用户名/昵称" clearable style="width:200px;margin-right:10px"
                    @keyup.enter="load" @clear="load" />
          <el-button type="primary" size="small" @click="createDialog = true">新建用户</el-button>
        </div>
      </template>
      <el-table :data="users" size="small" v-loading="loading" stripe border>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="username" label="用户名" width="140" />
        <el-table-column prop="nickname" label="昵称" width="140" />
        <el-table-column label="角色" width="130">
          <template #default="{ row }">
            <el-select :model-value="row.role" size="small" style="width:105px"
                       :disabled="row.id === meId" @change="(r) => changeRole(row, r)">
              <el-option label="管理员" value="admin" />
              <el-option label="策略员" value="analyst" />
              <el-option label="普通用户" value="user" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-switch :model-value="row.is_active" :disabled="row.id === meId"
                       @change="(v) => toggleActive(row, v)" />
          </template>
        </el-table-column>
        <el-table-column prop="holding_count" label="持仓数" width="80" />
        <el-table-column label="注册时间" min-width="150">
          <template #default="{ row }">{{ fmtDT(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openReset(row)">重置密码</el-button>
            <el-button size="small" link type="warning" @click="openEdit(row)">改昵称</el-button>
            <el-popconfirm title="删除该用户及其全部持仓/交易/报告数据,不可恢复?" confirm-button-text="删除"
                           cancel-button-text="取消" @confirm="remove(row)">
              <template #reference>
                <el-button size="small" link type="danger" :disabled="row.id === meId">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !users.length" description="暂无用户" :image-size="60">
        <template #description>
          <p>暂无用户,点右上「新建用户」或搜空时清空关键词</p>
          <p class="muted" style="font-size:12px">admin=全部权限 · analyst=选股工作台操作 · user=只读看板+自己持仓</p>
        </template>
      </el-empty>
      <div class="pager">
        <el-pagination layout="total, prev, pager, next" :total="total" :page-size="size"
                       :current-page="page" @current-change="(p) => { page = p; load() }" />
      </div>
    </el-card>

    <el-card class="page-card">
      <template #header>
        <div class="card-head">
          <span>邀请码(一次性,凭码注册)</span>
          <span class="grow" />
          <el-input-number v-model="inviteDays" :min="1" :max="90" size="small" style="width:110px;margin-right:10px" />
          <el-button type="primary" size="small" :loading="creatingInvite" @click="genInvite">生成邀请码</el-button>
        </div>
      </template>
      <el-table :data="invites" size="small" stripe border>
        <el-table-column prop="code" label="邀请码" width="170">
          <template #default="{ row }">
            <span class="num">{{ row.code }}</span>
            <el-button size="small" link style="margin-left:6px" @click="copy(row.code)">复制</el-button>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.used ? 'success' : row.active ? 'warning' : 'info'">
              {{ row.used ? '已使用' : row.active ? '待使用' : '已作废' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="used_by" label="使用者" width="120" />
        <el-table-column label="使用时间" width="140">
          <template #default="{ row }">{{ row.used_at ? fmtDT(row.used_at) : '-' }}</template>
        </el-table-column>
        <el-table-column label="过期时间" width="140">
          <template #default="{ row }">{{ row.expires_at ? fmtDT(row.expires_at) : '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button v-if="row.active && !row.used" size="small" link type="danger" @click="revoke(row)">作废</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!invites.length" description="暂无邀请码" :image-size="60">
        <template #description>
          <p>暂无邀请码,设置有效天数后点「生成邀请码」</p>
          <p class="muted" style="font-size:12px">邀请码一次性有效,凭码注册;用过即失效,不用可作废</p>
        </template>
      </el-empty>
    </el-card>

    <el-dialog v-model="createDialog" title="新建用户" width="440">
      <el-form ref="createRef" :model="createForm" :rules="createRules" label-width="90px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="createForm.username" placeholder="3-32 位" />
        </el-form-item>
        <el-form-item label="初始密码" prop="password">
          <el-input v-model="createForm.password" type="password" show-password placeholder="至少 8 位" />
        </el-form-item>
        <el-form-item label="昵称">
          <el-input v-model="createForm.nickname" placeholder="可选" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="createForm.role" style="width:200px">
            <el-option label="普通用户(只读+自己持仓)" value="user" />
            <el-option label="策略员(可操作选股工作台)" value="analyst" />
            <el-option label="管理员(全部权限)" value="admin" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resetDialog" title="重置密码" width="400">
      <el-form label-width="90px">
        <el-form-item label="用户">{{ resetTarget?.username }}</el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="resetPassword" type="password" show-password placeholder="至少 8 位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetDialog = false">取消</el-button>
        <el-button type="primary" :loading="resetting" @click="doReset">确认重置</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editDialog" title="修改昵称" width="380">
      <el-input v-model="editNickname" placeholder="新昵称" maxlength="32" />
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" @click="doEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { fmtDT } from '../utils/fmt'
import { useUserStore } from '../store'

const store = useUserStore()
const meId = computed(() => store.user?.id)

const users = ref([])
const invites = ref([])
const total = ref(0)
const page = ref(1)
const size = 20
const keyword = ref('')
const loading = ref(false)

const createDialog = ref(false)
const createRef = ref()
const creating = ref(false)
const createForm = ref({ username: '', password: '', nickname: '', role: 'user' })
const createRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 32, message: '用户名 3-32 位', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
}

const resetDialog = ref(false)
const resetTarget = ref(null)
const resetPassword = ref('')
const resetting = ref(false)

const editDialog = ref(false)
const editTarget = ref(null)
const editNickname = ref('')

const inviteDays = ref(7)
const creatingInvite = ref(false)

onMounted(() => { load(); loadInvites() })

async function load() {
  loading.value = true
  try {
    const r = await api.get('/api/auth/users', {
      params: { keyword: keyword.value, page: page.value, size },
    })
    users.value = r.items
    total.value = r.total
  } finally {
    loading.value = false
  }
}

async function loadInvites() {
  invites.value = await api.get('/api/auth/invites')
}

async function create() {
  await createRef.value.validate()
  creating.value = true
  try {
    await api.post('/api/auth/users', createForm.value)
    ElMessage.success('用户已创建')
    createDialog.value = false
    createForm.value = { username: '', password: '', nickname: '', role: 'user' }
    await load()
  } finally {
    creating.value = false
  }
}

async function changeRole(row, role) {
  try {
    await api.patch(`/api/auth/users/${row.id}`, { role })
    row.role = role
    ElMessage.success('角色已更新')
  } catch {
    await load()
  }
}

async function toggleActive(row, v) {
  try {
    await api.patch(`/api/auth/users/${row.id}`, { is_active: v })
    row.is_active = v
    ElMessage.success(v ? '已启用' : '已停用')
  } catch {
    await load()
  }
}

function openReset(row) {
  resetTarget.value = row
  resetPassword.value = ''
  resetDialog.value = true
}

async function doReset() {
  if (resetPassword.value.length < 8) return ElMessage.warning('密码至少 8 位')
  resetting.value = true
  try {
    await api.post(`/api/auth/users/${resetTarget.value.id}/reset-password`,
      { new_password: resetPassword.value })
    ElMessage.success('密码已重置')
    resetDialog.value = false
  } finally {
    resetting.value = false
  }
}

function openEdit(row) {
  editTarget.value = row
  editNickname.value = row.nickname
  editDialog.value = true
}

async function doEdit() {
  await api.patch(`/api/auth/users/${editTarget.value.id}`, { nickname: editNickname.value })
  ElMessage.success('昵称已更新')
  editDialog.value = false
  await load()
}

async function remove(row) {
  await api.delete(`/api/auth/users/${row.id}`)
  ElMessage.success('用户及其数据已删除')
  await load()
}

async function genInvite() {
  creatingInvite.value = true
  try {
    const r = await api.post('/api/auth/invites', { expires_days: inviteDays.value })
    ElMessage.success(`已生成:${r.code}`)
    await loadInvites()
  } finally {
    creatingInvite.value = false
  }
}

async function revoke(row) {
  await api.delete(`/api/auth/invites/${row.id}`)
  ElMessage.success('已作废')
  await loadInvites()
}

function copy(text) {
  navigator.clipboard?.writeText(text)
  ElMessage.success('已复制:' + text)
}
</script>
