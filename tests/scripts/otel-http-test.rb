require "opentelemetry/sdk"
require "opentelemetry/exporter/otlp"
require "opentelemetry/propagator/xray"
$stdout.sync = true
ep = "http://cloudwatch-agent.amazon-cloudwatch:4316/v1/traces"
ex = OpenTelemetry::Exporter::OTLP::Exporter.new(endpoint: ep)
OpenTelemetry::SDK.configure do |c|
  c.service_name = "hasher"
  c.id_generator = OpenTelemetry::Propagator::XRay::IDGenerator
end
t = OpenTelemetry.tracer_provider.tracer("diag")
s1 = nil
t.in_span("GET /diag-200", kind: :server) do |span|
  span.set_attribute("http.method", "GET")
  span.set_attribute("http.request.method", "GET")
  span.set_attribute("http.url", "http://10.0.11.146:80/diag-200")
  span.set_attribute("url.full", "http://10.0.11.146:80/diag-200")
  span.set_attribute("http.route", "/diag-200")
  span.set_attribute("http.response.status_code", 200)
  span.set_attribute("http.status_code", 200)
  s1 = span
end
r1 = ex.export([s1.to_span_data])
puts "200-with-http: export=#{r1} trace=#{s1.to_span_data.trace_id.unpack1('H*')}"
s2 = nil
t.in_span("GET /diag-500", kind: :server) do |span|
  span.set_attribute("http.method", "GET")
  span.set_attribute("http.request.method", "GET")
  span.set_attribute("http.url", "http://10.0.11.146:80/diag-500")
  span.set_attribute("url.full", "http://10.0.11.146:80/diag-500")
  span.set_attribute("http.route", "/diag-500")
  span.set_attribute("http.response.status_code", 500)
  span.set_attribute("http.status_code", 500)
  span.set_attribute("error.type", "ValidationError")
  span.status = OpenTelemetry::Trace::Status.error("fail")
  s2 = span
end
r2 = ex.export([s2.to_span_data])
puts "500-with-http: export=#{r2} trace=#{s2.to_span_data.trace_id.unpack1('H*')}"
