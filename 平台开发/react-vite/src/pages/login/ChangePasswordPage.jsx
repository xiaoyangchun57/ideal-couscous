import { App, Button, Form, Input, Typography } from 'antd';
import { LockOutlined } from '@ant-design/icons';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { useTheme } from '../../hooks/useTheme';
import { getSafeReturnTo } from '../../utils/authNavigation.js';

const { Title, Text } = Typography;

export default function ChangePasswordPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const location = useLocation();
  const { changePassword, loading } = useAuth();
  const { tokens } = useTheme();

  const onFinish = async (values) => {
    const result = await changePassword(values.currentPassword, values.newPassword);
    if (result.success) {
      message.success('新密码已生效');
      navigate(getSafeReturnTo(location.search), { replace: true });
      return;
    }
    message.error(result.error || '密码修改失败');
  };

  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24, background: tokens.colorBgLayout }}>
      <div style={{ width: '100%', maxWidth: 420, padding: 32, background: tokens.colorBgContainer, border: `1px solid ${tokens.colorBorder}`, borderRadius: 8 }}>
        <Title level={3} style={{ marginTop: 0, marginBottom: 8 }}>设置新密码</Title>
        <Text type="secondary">当前账号使用的是管理员签发的临时密码，设置新密码后才能进入系统。</Text>
        <Form layout="vertical" onFinish={onFinish} style={{ marginTop: 24 }} requiredMark={false}>
          <Form.Item name="currentPassword" label="临时密码" rules={[{ required: true, message: '请输入临时密码' }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" />
          </Form.Item>
          <Form.Item name="newPassword" label="新密码" rules={[
            { required: true, message: '请输入新密码' },
            { min: 8, message: '新密码至少8位' },
          ]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="confirmPassword" label="确认新密码" dependencies={['newPassword']} rules={[
            { required: true, message: '请再次输入新密码' },
            ({ getFieldValue }) => ({
              validator(_, value) {
                return !value || getFieldValue('newPassword') === value
                  ? Promise.resolve()
                  : Promise.reject(new Error('两次输入的新密码不一致'));
              },
            }),
          ]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>保存并进入系统</Button>
        </Form>
      </div>
    </div>
  );
}
