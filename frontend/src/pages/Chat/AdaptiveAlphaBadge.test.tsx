import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AdaptiveAlphaBadge } from "@/pages/Chat/TaskCard";

describe("AdaptiveAlphaBadge", () => {
  it("renders nothing when the feature is disabled", () => {
    const { container } = render(<AdaptiveAlphaBadge meta={{ enabled: false }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when meta is absent", () => {
    const { container } = render(<AdaptiveAlphaBadge />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the applied weight shift when the override took effect", () => {
    render(
      <AdaptiveAlphaBadge
        meta={{
          enabled: true,
          applied: true,
          static_alpha: 0.7,
          suggested_alpha: 0.452,
          n: 13,
        }}
      />,
    );
    expect(screen.getByText("自适应α已生效")).toBeInTheDocument();
    expect(screen.getByText(/quant权重 0\.70 → 0\.45/)).toBeInTheDocument();
    expect(screen.getByText(/有效样本 n=13/)).toBeInTheDocument();
  });

  it("explains why an enabled override did not apply", () => {
    render(
      <AdaptiveAlphaBadge meta={{ enabled: true, applied: false, reason: "not_applicable" }} />,
    );
    expect(screen.getByText("自适应α已开启")).toBeInTheDocument();
    expect(screen.getByText("样本不足，维持静态权重")).toBeInTheDocument();
  });
});
