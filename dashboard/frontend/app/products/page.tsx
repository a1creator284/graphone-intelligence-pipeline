import PlaceholderPage from "@/components/PlaceholderPage";

export default function Page() {
  return (
    <PlaceholderPage
      title="Products"
      description="AI products and models published by vendors."
      endpoint={"GET /api/products"}
    />
  );
}
