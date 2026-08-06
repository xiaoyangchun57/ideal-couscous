import { ArrowLeftOutlined, HomeOutlined } from '@ant-design/icons';
import { Button, Result, Space, Typography } from 'antd';
import { useLocation, useNavigate } from 'react-router-dom';

const { Text } = Typography;

export default function NotFoundPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const currentAddress = `${location.pathname}${location.search}${location.hash}`;

  return (
    <div id="main-content">
      <Result
        status="404"
        title="页面不存在"
        subTitle="当前地址不存在或已迁移。请检查链接，或返回上一页继续操作。"
        extra={(
          <Space wrap>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>返回上一页</Button>
            <Button type="primary" icon={<HomeOutlined />} onClick={() => navigate('/')}>返回驾驶舱</Button>
          </Space>
        )}
      >
        <div style={{ textAlign: 'center' }}>
          <Text type="secondary">当前地址：<Text code>{currentAddress}</Text></Text>
        </div>
      </Result>
    </div>
  );
}
