# Version: debug - Log every span export
require 'digest'
require 'sinatra/base'
require 'socket'
require 'json'
$stdout.sync = true
$stderr.sync = true
require 'opentelemetry/sdk'
require 'opentelemetry/exporter/otlp'
require 'opentelemetry/propagator/xray'
require 'opentelemetry/instrumentation/sinatra'

class LoggingExporter
  def initialize(exporter)
    @exporter = exporter
  end
  def export(spans, timeout: nil)
    spans.each do |s|
      sd = s.is_a?(OpenTelemetry::SDK::Trace::SpanData) ? s : s
      puts "SPAN_EXPORT: name=#{sd.name} status_code=#{sd.status.code} attrs=#{sd.attributes}"
    end
    result = @exporter.export(spans, timeout: timeout)
    puts "EXPORT_RESULT: #{result}"
    result
  end
  def force_flush(timeout: nil) = @exporter.force_flush(timeout: timeout)
  def shutdown(timeout: nil) = @exporter.shutdown(timeout: timeout)
end

otlp_ep = ENV.fetch('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://cloudwatch-agent.amazon-cloudwatch:4316') + '/v1/traces'
puts "OTEL: endpoint=#{otlp_ep}"
real_ex = OpenTelemetry::Exporter::OTLP::Exporter.new(endpoint: otlp_ep)
log_ex = LoggingExporter.new(real_ex)
OpenTelemetry::SDK.configure do |c|
  c.service_name = 'hasher'
  c.id_generator = OpenTelemetry::Propagator::XRay::IDGenerator
  c.propagators = [OpenTelemetry::Propagator::XRay::TextMapPropagator.new, OpenTelemetry::Trace::Propagation::TraceContext.text_map_propagator, OpenTelemetry::Baggage::Propagation.text_map_propagator]
  c.add_span_processor(OpenTelemetry::SDK::Trace::Export::SimpleSpanProcessor.new(log_ex))
  c.use 'OpenTelemetry::Instrumentation::Sinatra'
end
puts "OTEL: configured"

class HasherApp < Sinatra::Base
  set :protection, false
  set :bind, '0.0.0.0'
  set :port, 8080
  set :host_authorization, { permitted_hosts: [] }
  get '/' do
    "OK\n"
  end
  get '/error' do
    span = OpenTelemetry::Trace.current_span
    span.set_attribute('error.type', 'ValidationError')
    span.status = OpenTelemetry::Trace::Status.error('Validation failed')
    status 500
    "ERROR\n"
  end
  get '/slow' do
    span = OpenTelemetry::Trace.current_span
    span.set_attribute('processing.time_seconds', 1.0)
    sleep 1
    "SLOW OK\n"
  end
end
HasherApp.run!
