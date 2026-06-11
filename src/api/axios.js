import axios from 'axios'

export const api = axios.create({
  baseURL: '/api',
  timeout: 10000
})

// Add response interceptor to handle/log global errors
api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    console.error('API request failure:', {
      url: error.config?.url,
      method: error.config?.method,
      status: error.response?.status,
      data: error.response?.data,
      message: error.message
    })
    return Promise.reject(error)
  }
)

export default api
