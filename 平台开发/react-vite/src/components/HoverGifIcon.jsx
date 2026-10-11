import { useEffect, useRef, useState } from 'react';

/**
 * Hover 动态图标组件
 * - 默认显示静态 PNG（GIF 第一帧）
 * - 鼠标 hover 时切换到动态 GIF
 * - 用于导航菜单、操作按钮、工具栏等高频交互场景
 *
 * 两种触发模式：
 * - "self"（默认）：hover 图标自身才动，用于操作按钮等独立图标
 * - "parent"：hover 父级菜单项/按钮时就动，用于侧边栏导航等整行触发
 *
 * 操作图标（folder="actions"）：magnifier / pencil / trash-bin / eye
 * 导航图标（folder="nav"）：globe / warning-triangle / bar-chart / article /
 *   calendar-dots / list-rules / location-pin / api / wrench / person-cycling /
 *   image-mountain / avatar / clipboard-text
 */
export function HoverGifIcon({ name, size = 14, folder = 'actions', mode = 'self', style, alt = '' }) {
  const [hovered, setHovered] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (mode !== 'parent') return;
    const el = ref.current;
    if (!el) return;

    // 向上找最近的可交互父元素（菜单项/按钮等）
    const parent = el.closest('[role="menuitem"], .ant-menu-item, button, a');
    if (!parent) return;

    const onEnter = () => setHovered(true);
    const onLeave = () => setHovered(false);
    parent.addEventListener('mouseenter', onEnter);
    parent.addEventListener('mouseleave', onLeave);
    return () => {
      parent.removeEventListener('mouseenter', onEnter);
      parent.removeEventListener('mouseleave', onLeave);
    };
  }, [mode]);

  const handlers = mode === 'self'
    ? { onMouseEnter: () => setHovered(true), onMouseLeave: () => setHovered(false) }
    : {};

  return (
    <span ref={ref} style={{ display: 'inline-block', width: size, height: size, verticalAlign: '-2px', lineHeight: 0, position: 'relative', ...style }} {...handlers}>
      <img
        src={hovered ? `/icons/${folder}/${name}.gif` : `/icons/${folder}/${name}.png`}
        alt={alt}
        style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
        draggable={false}
      />
    </span>
  );
}

export default HoverGifIcon;
